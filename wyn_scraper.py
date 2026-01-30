#!/usr/bin/env python3
"""
WYN Distribution Product Scraper
Scrapes all products from wyndistribution.com and exports to Shopify-compatible CSV format.
"""

import os
import re
import csv
import time
import json
import hashlib
import logging
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    StaleElementReferenceException
)

# Configuration
BASE_URL = "https://wyndistribution.com"
LOGIN_URL = f"{BASE_URL}/my-account/"
SHOP_URL = f"{BASE_URL}/shop/"

# Login credentials
USERNAME = "joshua@oilslickpad.com"
PASSWORD = "710_Sl1ck"

# Output directories
OUTPUT_DIR = Path("output")
IMAGES_DIR = OUTPUT_DIR / "images"
DATA_DIR = OUTPUT_DIR / "data"

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('scraper.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class WYNScraper:
    """Scraper for WYN Distribution wholesale website."""

    def __init__(self, headless=True):
        """Initialize the scraper with Chrome WebDriver."""
        self.headless = headless
        self.driver = None
        self.session = requests.Session()
        self.products = []
        self.categories = []
        self.failed_products = []

        # Create output directories
        OUTPUT_DIR.mkdir(exist_ok=True)
        IMAGES_DIR.mkdir(exist_ok=True)
        DATA_DIR.mkdir(exist_ok=True)

    def setup_driver(self):
        """Configure and initialize Chrome WebDriver."""
        chrome_options = Options()
        if self.headless:
            chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

        self.driver = webdriver.Chrome(options=chrome_options)
        self.driver.implicitly_wait(10)
        logger.info("Chrome WebDriver initialized")

    def login(self):
        """Log into the WYN Distribution website."""
        logger.info("Attempting to log in...")
        self.driver.get(LOGIN_URL)
        time.sleep(2)

        try:
            # Wait for login form
            username_field = WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='username'], input#username"))
            )
            password_field = self.driver.find_element(By.CSS_SELECTOR, "input[name='password'], input#password")

            # Clear and enter credentials
            username_field.clear()
            username_field.send_keys(USERNAME)
            password_field.clear()
            password_field.send_keys(PASSWORD)

            # Find and click login button
            login_button = self.driver.find_element(By.CSS_SELECTOR, "button[name='login'], input[name='login'], button[type='submit']")
            login_button.click()

            # Wait for login to complete
            time.sleep(3)

            # Verify login success by checking for account dashboard or logout link
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".woocommerce-MyAccount-content, a[href*='logout'], .logged-in"))
            )

            logger.info("Successfully logged in!")

            # Transfer cookies to requests session for image downloads
            for cookie in self.driver.get_cookies():
                self.session.cookies.set(cookie['name'], cookie['value'])

            return True

        except TimeoutException:
            logger.error("Login failed - timeout waiting for elements")
            self.driver.save_screenshot(str(OUTPUT_DIR / "login_failed.png"))
            return False
        except NoSuchElementException as e:
            logger.error(f"Login failed - element not found: {e}")
            return False

    def get_categories(self):
        """Extract all product categories from the website."""
        logger.info("Fetching product categories...")
        self.driver.get(SHOP_URL)
        time.sleep(2)

        categories = []

        try:
            # Try multiple selector strategies for category links
            selectors = [
                "ul.product-categories a",
                ".widget_product_categories a",
                "nav.woocommerce-breadcrumb a",
                ".product-category a",
                "a[href*='/product-category/']"
            ]

            for selector in selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for elem in elements:
                        href = elem.get_attribute("href")
                        name = elem.text.strip()
                        if href and "/product-category/" in href and name:
                            categories.append({
                                "name": name,
                                "url": href
                            })
                except:
                    continue

            # Also try to get categories from the main navigation menu
            try:
                menu_items = self.driver.find_elements(By.CSS_SELECTOR, ".menu-item a[href*='product-category'], .nav-menu a[href*='product-category']")
                for item in menu_items:
                    href = item.get_attribute("href")
                    name = item.text.strip()
                    if href and name:
                        categories.append({
                            "name": name,
                            "url": href
                        })
            except:
                pass

            # Remove duplicates
            seen = set()
            unique_categories = []
            for cat in categories:
                if cat["url"] not in seen:
                    seen.add(cat["url"])
                    unique_categories.append(cat)

            self.categories = unique_categories
            logger.info(f"Found {len(self.categories)} categories")

            # If no categories found, we'll scrape the main shop page
            if not self.categories:
                logger.warning("No categories found, will scrape main shop page")
                self.categories = [{"name": "All Products", "url": SHOP_URL}]

            return self.categories

        except Exception as e:
            logger.error(f"Error fetching categories: {e}")
            self.categories = [{"name": "All Products", "url": SHOP_URL}]
            return self.categories

    def get_all_product_urls(self):
        """Get all product URLs from all category pages."""
        product_urls = set()

        # Start with main shop page
        pages_to_scrape = [SHOP_URL]

        # Add all category pages
        for cat in self.categories:
            pages_to_scrape.append(cat["url"])

        for page_url in pages_to_scrape:
            logger.info(f"Scraping product list from: {page_url}")
            urls = self._get_products_from_listing(page_url)
            product_urls.update(urls)

        logger.info(f"Found {len(product_urls)} unique product URLs")
        return list(product_urls)

    def _get_products_from_listing(self, listing_url):
        """Get all product URLs from a category/listing page, handling pagination."""
        product_urls = set()
        current_url = listing_url
        page_num = 1

        while current_url:
            logger.info(f"  Page {page_num}: {current_url}")
            self.driver.get(current_url)
            time.sleep(2)

            # Find product links
            try:
                product_selectors = [
                    "a.woocommerce-LoopProduct-link",
                    ".product a.woocommerce-loop-product__link",
                    ".products .product a[href*='/product/']",
                    "ul.products li.product a",
                    ".product-item a[href*='/product/']",
                    "a[href*='/product/']"
                ]

                for selector in product_selectors:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for elem in elements:
                        href = elem.get_attribute("href")
                        if href and "/product/" in href:
                            product_urls.add(href)
                    if elements:
                        break

            except Exception as e:
                logger.error(f"Error finding products: {e}")

            # Check for next page
            current_url = None
            try:
                next_selectors = [
                    "a.next.page-numbers",
                    ".woocommerce-pagination a.next",
                    "a[rel='next']",
                    ".pagination a.next"
                ]
                for selector in next_selectors:
                    try:
                        next_link = self.driver.find_element(By.CSS_SELECTOR, selector)
                        current_url = next_link.get_attribute("href")
                        page_num += 1
                        break
                    except NoSuchElementException:
                        continue
            except:
                pass

        return product_urls

    def scrape_product(self, product_url):
        """Scrape all details from a single product page."""
        logger.info(f"Scraping product: {product_url}")

        try:
            self.driver.get(product_url)
            time.sleep(2)

            product = {
                "url": product_url,
                "handle": "",
                "title": "",
                "body_html": "",
                "vendor": "",
                "product_type": "",
                "tags": "",
                "published": "TRUE",
                "option1_name": "",
                "option1_value": "",
                "option2_name": "",
                "option2_value": "",
                "option3_name": "",
                "option3_value": "",
                "variant_sku": "",
                "variant_price": "",
                "variant_compare_at_price": "",
                "variant_inventory_qty": "",
                "variant_weight": "",
                "variant_weight_unit": "lb",
                "image_src": "",
                "image_alt_text": "",
                "additional_images": [],
                "categories": [],
                "attributes": {},
                "meta_title": "",
                "meta_description": ""
            }

            # Extract title
            try:
                title_elem = self.driver.find_element(By.CSS_SELECTOR, "h1.product_title, .product-title h1, h1.entry-title")
                product["title"] = title_elem.text.strip()
                product["handle"] = self._generate_handle(product["title"])
            except NoSuchElementException:
                logger.warning(f"Could not find title for {product_url}")

            # Extract price
            try:
                price_selectors = [
                    ".price .woocommerce-Price-amount bdi",
                    ".price .amount",
                    "p.price span.woocommerce-Price-amount",
                    ".summary .price"
                ]
                for selector in price_selectors:
                    try:
                        price_elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                        price_text = price_elem.text.strip()
                        product["variant_price"] = self._clean_price(price_text)
                        break
                    except NoSuchElementException:
                        continue

                # Check for sale price (compare at price)
                try:
                    regular_price = self.driver.find_element(By.CSS_SELECTOR, ".price del .amount, del .woocommerce-Price-amount")
                    product["variant_compare_at_price"] = self._clean_price(regular_price.text.strip())
                except NoSuchElementException:
                    pass

            except Exception as e:
                logger.warning(f"Could not extract price: {e}")

            # Extract SKU
            try:
                sku_selectors = [
                    ".sku_wrapper .sku",
                    ".product_meta .sku",
                    "span.sku"
                ]
                for selector in sku_selectors:
                    try:
                        sku_elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                        product["variant_sku"] = sku_elem.text.strip()
                        break
                    except NoSuchElementException:
                        continue
            except:
                pass

            # Extract description
            try:
                desc_selectors = [
                    ".woocommerce-product-details__short-description",
                    ".product-short-description",
                    "#tab-description",
                    ".woocommerce-Tabs-panel--description"
                ]
                descriptions = []
                for selector in desc_selectors:
                    try:
                        desc_elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                        desc_html = desc_elem.get_attribute("innerHTML")
                        if desc_html:
                            descriptions.append(desc_html.strip())
                    except NoSuchElementException:
                        continue
                product["body_html"] = "\n".join(descriptions)
            except:
                pass

            # Extract categories/tags
            try:
                cat_selectors = [
                    ".posted_in a",
                    ".product_meta .posted_in a",
                    "span.posted_in a"
                ]
                for selector in cat_selectors:
                    try:
                        cat_elems = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        product["categories"] = [elem.text.strip() for elem in cat_elems]
                        product["product_type"] = product["categories"][0] if product["categories"] else ""
                        product["tags"] = ", ".join(product["categories"])
                        break
                    except:
                        continue
            except:
                pass

            # Extract brand/vendor
            try:
                brand_selectors = [
                    ".product_meta .tagged_as a",
                    "a[href*='/brand/']",
                    ".brand a",
                    "span.tagged_as a"
                ]
                for selector in brand_selectors:
                    try:
                        brand_elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                        product["vendor"] = brand_elem.text.strip()
                        break
                    except NoSuchElementException:
                        continue
            except:
                pass

            # Extract main image
            try:
                img_selectors = [
                    ".woocommerce-product-gallery__image img",
                    ".product-gallery img",
                    ".wp-post-image",
                    "img.attachment-shop_single"
                ]
                for selector in img_selectors:
                    try:
                        img_elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                        img_src = img_elem.get_attribute("src") or img_elem.get_attribute("data-src")
                        if img_src:
                            # Get high-res version if available
                            img_src = img_elem.get_attribute("data-large_image") or img_src
                            product["image_src"] = img_src
                            product["image_alt_text"] = img_elem.get_attribute("alt") or product["title"]
                            break
                    except NoSuchElementException:
                        continue
            except:
                pass

            # Extract additional gallery images
            try:
                gallery_selectors = [
                    ".woocommerce-product-gallery__image:not(:first-child) img",
                    ".product-gallery .gallery-item img",
                    ".woocommerce-product-gallery .flex-control-thumbs img"
                ]
                for selector in gallery_selectors:
                    try:
                        gallery_imgs = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        for img in gallery_imgs:
                            img_src = img.get_attribute("data-large_image") or img.get_attribute("src") or img.get_attribute("data-src")
                            if img_src and img_src != product["image_src"]:
                                product["additional_images"].append(img_src)
                        if gallery_imgs:
                            break
                    except:
                        continue
            except:
                pass

            # Extract product variations/options if any
            try:
                variation_form = self.driver.find_element(By.CSS_SELECTOR, "form.variations_form")
                variations_data = variation_form.get_attribute("data-product_variations")
                if variations_data:
                    product["variations"] = json.loads(variations_data)
            except NoSuchElementException:
                product["variations"] = []
            except:
                product["variations"] = []

            # Extract additional attributes from product tabs or meta
            try:
                attr_rows = self.driver.find_elements(By.CSS_SELECTOR, ".woocommerce-product-attributes tr, .shop_attributes tr")
                for row in attr_rows:
                    try:
                        label = row.find_element(By.CSS_SELECTOR, "th").text.strip()
                        value = row.find_element(By.CSS_SELECTOR, "td").text.strip()
                        product["attributes"][label] = value
                    except:
                        continue
            except:
                pass

            # Extract weight if available
            try:
                weight_elem = self.driver.find_element(By.CSS_SELECTOR, ".product_weight")
                product["variant_weight"] = weight_elem.text.strip()
            except NoSuchElementException:
                pass

            # Meta information
            try:
                meta_title = self.driver.find_element(By.CSS_SELECTOR, "meta[property='og:title']")
                product["meta_title"] = meta_title.get_attribute("content")
            except:
                product["meta_title"] = product["title"]

            try:
                meta_desc = self.driver.find_element(By.CSS_SELECTOR, "meta[name='description'], meta[property='og:description']")
                product["meta_description"] = meta_desc.get_attribute("content")
            except:
                pass

            self.products.append(product)
            return product

        except Exception as e:
            logger.error(f"Error scraping product {product_url}: {e}")
            self.failed_products.append({"url": product_url, "error": str(e)})
            return None

    def download_image(self, image_url, product_handle, index=0):
        """Download an image and return the local filename."""
        if not image_url:
            return None

        try:
            # Generate filename
            ext = os.path.splitext(urlparse(image_url).path)[1] or ".jpg"
            filename = f"{product_handle}_{index}{ext}"
            filepath = IMAGES_DIR / filename

            # Skip if already downloaded
            if filepath.exists():
                return str(filepath)

            # Download with session cookies
            response = self.session.get(image_url, timeout=30)
            response.raise_for_status()

            with open(filepath, 'wb') as f:
                f.write(response.content)

            logger.info(f"Downloaded: {filename}")
            return str(filepath)

        except Exception as e:
            logger.error(f"Failed to download image {image_url}: {e}")
            return None

    def download_all_images(self):
        """Download all product images."""
        logger.info("Downloading product images...")

        for product in self.products:
            handle = product.get("handle", "unknown")

            # Download main image
            if product.get("image_src"):
                local_path = self.download_image(product["image_src"], handle, 0)
                product["local_image_path"] = local_path

            # Download additional images
            product["local_additional_images"] = []
            for i, img_url in enumerate(product.get("additional_images", []), 1):
                local_path = self.download_image(img_url, handle, i)
                if local_path:
                    product["local_additional_images"].append(local_path)

            time.sleep(0.5)  # Rate limiting

    def export_to_shopify_csv(self, filename="shopify_products.csv"):
        """Export products to Shopify-compatible CSV format."""
        filepath = DATA_DIR / filename

        # Shopify CSV headers
        headers = [
            "Handle", "Title", "Body (HTML)", "Vendor", "Product Category", "Type", "Tags",
            "Published", "Option1 Name", "Option1 Value", "Option2 Name", "Option2 Value",
            "Option3 Name", "Option3 Value", "Variant SKU", "Variant Grams",
            "Variant Inventory Tracker", "Variant Inventory Qty", "Variant Inventory Policy",
            "Variant Fulfillment Service", "Variant Price", "Variant Compare At Price",
            "Variant Requires Shipping", "Variant Taxable", "Variant Barcode",
            "Image Src", "Image Position", "Image Alt Text", "Gift Card",
            "SEO Title", "SEO Description", "Google Shopping / Google Product Category",
            "Google Shopping / Gender", "Google Shopping / Age Group", "Google Shopping / MPN",
            "Google Shopping / AdWords Grouping", "Google Shopping / AdWords Labels",
            "Google Shopping / Condition", "Google Shopping / Custom Product",
            "Google Shopping / Custom Label 0", "Google Shopping / Custom Label 1",
            "Google Shopping / Custom Label 2", "Google Shopping / Custom Label 3",
            "Google Shopping / Custom Label 4", "Variant Image", "Variant Weight Unit",
            "Variant Tax Code", "Cost per item", "Price / International",
            "Compare At Price / International", "Status"
        ]

        rows = []

        for product in self.products:
            handle = product.get("handle", "")

            # Check if product has variations
            variations = product.get("variations", [])

            if variations:
                # Product with variations - create multiple rows
                for i, var in enumerate(variations):
                    row = self._create_shopify_row(product, variation=var, is_first=(i == 0))
                    rows.append(row)
            else:
                # Simple product
                row = self._create_shopify_row(product)
                rows.append(row)

            # Add rows for additional images
            for img_idx, img_url in enumerate(product.get("additional_images", []), 2):
                img_row = {key: "" for key in headers}
                img_row["Handle"] = handle
                img_row["Image Src"] = img_url
                img_row["Image Position"] = img_idx
                img_row["Image Alt Text"] = product.get("title", "")
                rows.append(img_row)

        # Write CSV
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)

        logger.info(f"Exported {len(self.products)} products to {filepath}")
        return filepath

    def _create_shopify_row(self, product, variation=None, is_first=True):
        """Create a single Shopify CSV row."""
        row = {
            "Handle": product.get("handle", ""),
            "Title": product.get("title", "") if is_first else "",
            "Body (HTML)": product.get("body_html", "") if is_first else "",
            "Vendor": product.get("vendor", "") if is_first else "",
            "Product Category": product.get("product_type", "") if is_first else "",
            "Type": product.get("product_type", "") if is_first else "",
            "Tags": product.get("tags", "") if is_first else "",
            "Published": "TRUE" if is_first else "",
            "Option1 Name": "",
            "Option1 Value": "",
            "Option2 Name": "",
            "Option2 Value": "",
            "Option3 Name": "",
            "Option3 Value": "",
            "Variant SKU": product.get("variant_sku", ""),
            "Variant Grams": "",
            "Variant Inventory Tracker": "shopify",
            "Variant Inventory Qty": product.get("variant_inventory_qty", ""),
            "Variant Inventory Policy": "deny",
            "Variant Fulfillment Service": "manual",
            "Variant Price": product.get("variant_price", ""),
            "Variant Compare At Price": product.get("variant_compare_at_price", ""),
            "Variant Requires Shipping": "TRUE",
            "Variant Taxable": "TRUE",
            "Variant Barcode": "",
            "Image Src": product.get("image_src", "") if is_first else "",
            "Image Position": "1" if is_first and product.get("image_src") else "",
            "Image Alt Text": product.get("image_alt_text", "") if is_first else "",
            "Gift Card": "FALSE" if is_first else "",
            "SEO Title": product.get("meta_title", "") if is_first else "",
            "SEO Description": product.get("meta_description", "") if is_first else "",
            "Variant Weight Unit": product.get("variant_weight_unit", "lb"),
            "Status": "active" if is_first else ""
        }

        # Handle variations
        if variation:
            attrs = variation.get("attributes", {})
            option_num = 1
            for attr_name, attr_value in attrs.items():
                if option_num <= 3:
                    row[f"Option{option_num} Name"] = attr_name.replace("attribute_pa_", "").replace("attribute_", "").replace("-", " ").title()
                    row[f"Option{option_num} Value"] = attr_value
                    option_num += 1

            row["Variant SKU"] = variation.get("sku", "")
            row["Variant Price"] = self._clean_price(str(variation.get("display_price", "")))
            if variation.get("display_regular_price"):
                row["Variant Compare At Price"] = self._clean_price(str(variation.get("display_regular_price", "")))
            if variation.get("image", {}).get("url"):
                row["Variant Image"] = variation["image"]["url"]

        return row

    def export_to_json(self, filename="products.json"):
        """Export products to JSON format for backup/reference."""
        filepath = DATA_DIR / filename

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.products, f, indent=2, ensure_ascii=False)

        logger.info(f"Exported products to {filepath}")
        return filepath

    def export_failed_products(self, filename="failed_products.json"):
        """Export list of products that failed to scrape."""
        if not self.failed_products:
            return None

        filepath = DATA_DIR / filename
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.failed_products, f, indent=2)

        logger.info(f"Exported {len(self.failed_products)} failed products to {filepath}")
        return filepath

    @staticmethod
    def _generate_handle(title):
        """Generate a Shopify-compatible handle from product title."""
        if not title:
            return ""
        handle = title.lower()
        handle = re.sub(r'[^a-z0-9\s-]', '', handle)
        handle = re.sub(r'[\s_]+', '-', handle)
        handle = re.sub(r'-+', '-', handle)
        handle = handle.strip('-')
        return handle[:255]  # Shopify handle max length

    @staticmethod
    def _clean_price(price_str):
        """Extract numeric price from price string."""
        if not price_str:
            return ""
        # Remove currency symbols and extract number
        cleaned = re.sub(r'[^\d.]', '', str(price_str))
        try:
            return str(float(cleaned))
        except ValueError:
            return ""

    def run(self):
        """Execute the full scraping process."""
        start_time = datetime.now()
        logger.info("=" * 50)
        logger.info("Starting WYN Distribution scraper")
        logger.info("=" * 50)

        try:
            # Setup
            self.setup_driver()

            # Login
            if not self.login():
                logger.error("Failed to login. Exiting.")
                return False

            # Get categories
            self.get_categories()

            # Get all product URLs
            product_urls = self.get_all_product_urls()

            if not product_urls:
                logger.error("No products found. Exiting.")
                return False

            # Scrape each product
            logger.info(f"Scraping {len(product_urls)} products...")
            for i, url in enumerate(product_urls, 1):
                logger.info(f"Progress: {i}/{len(product_urls)}")
                self.scrape_product(url)
                time.sleep(1)  # Rate limiting

            # Download images
            self.download_all_images()

            # Export data
            self.export_to_shopify_csv()
            self.export_to_json()
            self.export_failed_products()

            # Summary
            elapsed = datetime.now() - start_time
            logger.info("=" * 50)
            logger.info("Scraping complete!")
            logger.info(f"Total products scraped: {len(self.products)}")
            logger.info(f"Failed products: {len(self.failed_products)}")
            logger.info(f"Time elapsed: {elapsed}")
            logger.info(f"Output directory: {OUTPUT_DIR}")
            logger.info("=" * 50)

            return True

        except Exception as e:
            logger.error(f"Scraper error: {e}")
            raise
        finally:
            if self.driver:
                self.driver.quit()


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="WYN Distribution Product Scraper")
    parser.add_argument("--headless", action="store_true", default=True,
                        help="Run browser in headless mode (default: True)")
    parser.add_argument("--no-headless", action="store_true",
                        help="Run browser with visible window")
    parser.add_argument("--no-images", action="store_true",
                        help="Skip downloading images")

    args = parser.parse_args()

    headless = not args.no_headless

    scraper = WYNScraper(headless=headless)

    try:
        success = scraper.run()
        return 0 if success else 1
    except KeyboardInterrupt:
        logger.info("Scraper interrupted by user")
        return 1
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
