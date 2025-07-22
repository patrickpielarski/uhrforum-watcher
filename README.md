# Uhrforum Watcher
This script reads the RSS feed of [uhrforum.de](https://uhrforum.de/) and looks for new posts in sub forum "Angbote", filters them by keywords defined in the .env file and then pushes a push notification via [Pushover](https://pushover.net/).
Pushover API Keys have to be specified in .env file.

The following Python libraries must be installed:
- requests
- dotenv
- selenium
- BeautifulSoup4