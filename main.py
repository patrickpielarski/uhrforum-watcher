import logging
import os
import time
import xml.etree.ElementTree as ET
import re
import requests
import urllib3
import html
from dotenv import load_dotenv
from selenium import webdriver
from bs4 import BeautifulSoup
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException

# Load environment variables from .env file
load_dotenv()

# Suppress SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logging.basicConfig(filename="rss_watcher.log", level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

# Read Pushover credentials from environment variables
PUSHOVER_TOKEN = os.getenv("PUSHOVER_TOKEN")
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY")

# Read the wait time between feed checks from the environment variable
# If not provided, default to 60 seconds
WAIT_TIME = int(os.getenv("WAIT_TIME", 120))

headers = {
    'sec-ch-ua-platform' : 'macOS',
    'Referer' : 'https://uhrforum.de/',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
}

seen_posts = set()

first_run = True
is_error = False

def send_initial_notification():
    response = requests.post(
        "https://api.pushover.net/1/messages.json",
        data={
            "token": PUSHOVER_TOKEN,
            "user": PUSHOVER_USER_KEY,
            "message": "Watcher is active and monitoring the RSS feed.",
            "title": "Uhrforum Watcher"
        }
    )
    logging.info("~ Uhrforum Watcher started ~")

def send_error_notification(error_message):
    message = f"Error occured: {error_message}"
    response = requests.post(
        "https://api.pushover.net/1/messages.json",
        data={
            "token": PUSHOVER_TOKEN,
            "user": PUSHOVER_USER_KEY,
            "message": message,
            "title": "Uhrforum Watcher"
        }
    )
    logging.info("Error notification sent")

def send_notification(title, link):
    message = f"New Post: {title}\nLink: {link}"
    response = requests.post(
        "https://api.pushover.net/1/messages.json",
        data={
            "token": PUSHOVER_TOKEN,
            "user": PUSHOVER_USER_KEY,
            "message": message,
            "title": "New UhrForum Post"
        }
    )
    if response.status_code == 200:
        logging.info(f"Notification sent for post: {title}")
    else:
        logging.info(f"Failed to send notification: {response.status_code}")


def check_feed():
    global first_run
    global is_error

    filter_keywords = update_filter_keywords()
    # Fetch the RSS feed while bypassing SSL verification and using custom headers
    options = Options()
    # Run Chrome in headless mode so it doesn't steal focus
    # Use the modern headless mode where available
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    # Provide the same user-agent as used for requests
    options.add_argument(f"user-agent={headers['User-Agent']}")
    driver = webdriver.Chrome(options=options)
    try:
        url = "https://uhrforum.de/forums/-/index.rss"
        driver.get(url)
    except WebDriverException as e:
        logging.error(f"WebDriver error occurred: {e}")
        driver.quit()
        return

    rss_content = driver.page_source
    driver.quit()
    
    # Check if the content is not empty
    if not rss_content:
        logging.error("No content found in the response.")
        return
    
    if "403 Forbidden" in rss_content or "not authorized" in rss_content:
        logging.error("Access denied or forbidden error detected!")
        return
    
    soup = BeautifulSoup(rss_content, "html.parser")
    pre_tag = soup.find("pre")

    if not pre_tag:
        if not is_error:
            is_error = True
            send_error_notification("No <pre> tag found — RSS feed not formatted as expected")

        logging.error("No <pre> tag found — RSS feed not formatted as expected")
        return

    is_error = False
    rss_escaped = pre_tag.text

    # Unescape HTML entities
    rss_unescaped = html.unescape(rss_escaped)
    safe_rss = fix_common_xml_problems(rss_unescaped)
    # Parse the XML content
    try:
        root = ET.fromstring(safe_rss)
    except ET.ParseError as e:
        match = re.search(r"line (\d+), column (\d+)", str(e))
        if match:
            line_num = int(match.group(1))
            col_num = int(match.group(2))

            # Get the specific line from the XML
            lines = safe_rss.splitlines()

            logging.error(f"XML Parse Error at line {line_num}, column {col_num}:")
            if 0 < line_num <= len(lines):
                error_line = lines[line_num - 1]
                logging.error(f"{line_num:4d}: {error_line}")
                # Optionally print a marker to show column
                logging.error("     " + " " * (col_num - 1) + "^")
            else:
                logging.error("Line number out of range.")
        else:
            logging.error(f"XML Parse Error: {e}")
        return

    channel = root.find('channel')
    # Collect only items in the "Angebote" category
    angebote_items = []

    for item in channel.findall('item'):
        categories = item.findall('category')
        for cat in categories:
            domain = cat.attrib.get('domain')
            text = cat.text.strip() if cat.text else ""

            if domain == "https://uhrforum.de/forums/angebote.11/" and text == "Angebote":
                angebote_items.append(item)
                break  # Match found, no need to check other categories

    for item in angebote_items:
        title = item.find('title').text
        link = item.find('link').text
        guid = item.find('guid').text
        # If it's the first run, just mark posts as seen without sending notifications
        if first_run:
            seen_posts.add(guid)
        # Send notification for new posts only after the first run
        elif guid not in seen_posts:
            # Check if filtering is required
            if not filter_keywords or any(keyword in title.lower() for keyword in filter_keywords):
                send_notification(title, link)
            seen_posts.add(guid)

    # After the first run, set first_run to False
    if first_run:
        first_run = False


# Main loop to run the feed check every minute
def monitor_feed():
    while True:
        try:
            logging.info("Checking for new posts...")
            WAIT_TIME = int(os.getenv("WAIT_TIME", 120))
            check_feed()
        except Exception as e:
            logging.error(f"Error during feed check: {e}", exc_info=True)
        time.sleep(WAIT_TIME)

def escape_xml_text_nodes(elem):
    """
    Recursively escape <, >, & in text and tail content of an ElementTree element
    """
    if elem.text:
        elem.text = html.escape(elem.text)
    for child in elem:
        escape_xml_text_nodes(child)
        if child.tail:
            child.tail = html.escape(child.tail)


def fix_common_xml_problems(xml_text: str) -> str:
    try:
        # Try to parse it first — if valid, no changes needed
        root = ET.fromstring(xml_text)
        return xml_text  # already fine
    except ET.ParseError:
        pass  # we'll fix it below

    # Try escaping ampersands not in proper entities
    cleaned = re.sub(r'&(?!#?\w+;)', '&amp;', xml_text)

    # Try parsing again
    try:
        root = ET.fromstring(cleaned)
        escape_xml_text_nodes(root)
        return ET.tostring(root, encoding='unicode')
    except ET.ParseError as e:
        # Fallback: just return the ampersand-cleaned one
        return cleaned

def update_filter_keywords():
    # Read filter keywords from the environment variable
    FILTER_KEYWORDS = os.getenv("FILTER_KEYWORDS")

    # If FILTER_KEYWORDS is not None, split it into a list, else keep it as an empty list
    if FILTER_KEYWORDS:
        logging.info("Current filter: " + FILTER_KEYWORDS)
        filter_keywords = [keyword.strip().lower() for keyword in FILTER_KEYWORDS.split(',')]
    else:
        filter_keywords = []
    return filter_keywords

if __name__ == "__main__":
    send_initial_notification()
    monitor_feed()
