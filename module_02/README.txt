Name: Shane Battles (sbattl15)
Module Info: Module 2 Assignment: Web Scraping

scrape.py: This is the main program for the website. It uses BeautifulSoup and urllib to scrape thegradcafe.com. It uses multithreading to scrape 8 pages at a time and speed up the process, which is valuable since it is set to scrape 40,000 entries. First it checks robots.txt to ensure there is permission to fetch data from Grad Cafe. Once permission is confirmed the program begins scraping a page at a time until it hits the desired number of entries based on the variable MAX_ENTRIES. Using a very long list of regex expressions, the desired data fields are pulled for each survey and added to a JSON file called "applicant_data". Some minor cleaning and organizing occurs when creating the JSON, but most of that is left to clean.py

clean.py: This program takes "applicant_data.json" and performs baseline data cleaning and formatting to organize each survey's data into the baseline we want to use later. This is done through an extensive list of parsers that identify values and reorganize them as desired. The program then creates a JSON file called "cleaned_applicant_data" which will be used in the LLM cleaning steps inside app.py.

app.py: This program (which was provided to us) uses an LLM to further clean and organize the data from our web scraping. Using lists of data provided in the "llm_hosting" folder, it creates two new fields that are useful for future analyses.

Known bugs:
scrape.py: There was a HTTP 403 error when accessing thegradcafe.com. I assessed that my IP was blocked due to accessing the site too many times. Additionally, there were some issues with the multithreading that prevented it from stopping at the desired number of entries from MAX_ENTRIES. I think I fixed the issue, but my IP was banned before I could test and confirm it.
