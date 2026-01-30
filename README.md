# WYN Distribution Scraper

Python scraper for extracting product data from wyndistribution.com for Shopify import.

## Setup

1. Install Python 3.8+ if not already installed

2. Install Chrome browser if not already installed

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Install ChromeDriver (should match your Chrome version):
```bash
# On Ubuntu/Debian:
sudo apt-get install chromium-chromedriver

# Or use webdriver-manager (auto-downloads correct version):
pip install webdriver-manager
```

## Usage

Run the scraper:
```bash
python wyn_scraper.py
```

### Options

- `--no-headless` - Run with visible browser window (useful for debugging)
- `--no-images` - Skip downloading product images

### Examples

```bash
# Run in headless mode (default)
python wyn_scraper.py

# Run with visible browser for debugging
python wyn_scraper.py --no-headless
```

## Output

The scraper creates an `output/` directory with:

```
output/
├── data/
│   ├── shopify_products.csv    # Shopify-ready CSV import file
│   ├── products.json           # Full product data backup
│   └── failed_products.json    # Products that failed to scrape
├── images/                     # Downloaded product images
└── login_failed.png            # Screenshot if login fails
```

## Shopify Import

1. Go to Shopify Admin > Products > Import
2. Upload `output/data/shopify_products.csv`
3. Review the import preview
4. Click "Import products"

### CSV Fields Mapped

| Field | Description |
|-------|-------------|
| Handle | URL-friendly product identifier |
| Title | Product name |
| Body (HTML) | Product description |
| Vendor | Brand name |
| Type | Product category |
| Tags | Product tags for filtering |
| Variant SKU | Stock keeping unit |
| Variant Price | Product price |
| Variant Compare At Price | Original/sale price |
| Image Src | Main product image URL |
| SEO Title | Meta title for search engines |
| SEO Description | Meta description |

## Configuration

Edit credentials in `wyn_scraper.py` if needed:

```python
USERNAME = "your_email@example.com"
PASSWORD = "your_password"
```

## Troubleshooting

### Login fails
- Verify credentials are correct
- Check `output/login_failed.png` for the error screen
- Try running with `--no-headless` to watch the login process

### No products found
- The site structure may have changed
- Run with `--no-headless` and check if categories load
- Check `scraper.log` for errors

### ChromeDriver version mismatch
```bash
pip install webdriver-manager
```
Then modify the script to use:
```python
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service)
```

## Logs

Check `scraper.log` for detailed execution logs and any errors.
