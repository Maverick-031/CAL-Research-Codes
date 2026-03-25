# CAL-Research-Codes

A collection of web scraping tools for e-commerce platforms in Bangladesh. This repository contains scripts for extracting product data from popular online shopping sites for research and analysis purposes.

## 📁 Repository Structure

```
CAL-Research-Codes/
├── Arogga/           # Arogga.com product scraper
│   ├── arogga_scraper.py
│   ├── links.xlsx
│   └── requirements.txt
├── Chaldal/          # Chaldal.com product scraper
│   ├── Chaldal_scrape.py
│   ├── links.xlsx
│   └── requirements.txt
├── LICENSE
└── README.md
```

## 🛒 Scrapers

### Arogga Scraper

Scrapes product information from Arogga.com categories.

**Features:**
- Reads category URLs from `links.xlsx`
- Extracts product details (name, price, description, etc.)
- Saves output to date-stamped CSV files in organized folder structure

**Requirements:**
```bash
pip install selenium openpyxl webdriver-manager
```

**Usage:**
```bash
cd Arogga
python arogga_scraper.py
# Or with custom links file:
python arogga_scraper.py --links my_links.xlsx
```

### Chaldal Scraper

Scrapes product information from Chaldal.com categories.

**Features:**
- Reads category URLs from `links.xlsx`
- Extracts comprehensive product data
- Outputs to CSV with date stamps

**Requirements:**
```bash
pip install selenium pandas openpyxl beautifulsoup4
```

**System Dependencies:**
```bash
# Debian/Ubuntu
sudo apt-get install -y chromium-browser chromium-chromedriver

# macOS
brew install --cask chromedriver
```

**Usage:**
```bash
cd Chaldal
python Chaldal_scrape.py
# Advanced usage:
python Chaldal_scrape.py --input my_links.xlsx --url-column "Category URL"
```

## ⚙️ Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd CAL-Research-Codes
```

2. Install dependencies for each scraper:
```bash
# For Arogga
cd Arogga
pip install -r requirements.txt

# For Chaldal
cd ../Chaldal
pip install -r requirements.txt
```

## 📝 Notes

- Both scrapers use Selenium for browser automation
- Output files are saved in `output/<YEAR>/<MM-Month>/` directories with date-stamped filenames
- Ensure you have appropriate permissions and comply with the target websites' terms of service before using these scrapers

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## ⚠️ Disclaimer

These tools are provided for research purposes only. Please ensure compliance with:
- Target websites' Terms of Service
- Robots.txt policies
- Applicable laws and regulations regarding web scraping