import binascii
import csv
import datetime
import io
import iocextract
import json
import logging
import math
import os
import pandas as pd
import pytesseract
import requests
import time


from azure.cosmos import CosmosClient
from datetime import datetime
from dateutil.relativedelta import relativedelta
from pathlib import Path
from PIL import Image
from urlextract import URLExtract


####################
# GLOBAL VARIABLES #
####################

MAX_POST_REQ_NETCRAFT = 1000 

# Supported file types
img_exts = ['jpg', 'png', 'gif', 'bmp', 'tiff']
doc_exts = ['txt', 'csv', 'doc', 'rtf']

##########################################################################
#
# Function name: main
# Input: Blob that triggered this function to be run.
# Output: TBD
#
# Purpose: Extract and action reported URLs for categorization.
#
##########################################################################
def main():

    input_file = Path('/input') / os.environ.get('INPUT_FILE')
    print (input_file)

    if input_file:
        # Extract URLs from blob and dedup
        file_content    = access_input_file(input_file)
        url_list        = extract_URLs(file_content)

        print("\n***** URLs sent to Netcraft *****")
        for url in url_list:
            print (url)

        if len(url_list) > 0:
            uuid = submit_URLs_Netcraft(url_list)
            print ("Netcraft UUID: " + uuid)
            #netcraft_uuids.append(uuid)

            if uuid == "0000":
                print ("Error, UUID is set to default value of 0000\n")
            else:
                update_cosmos_db(uuid, len(url_list), url_list)
        else:
            print ("No new URLs to submit to Netcraft")

    else:
        print ("Input file not found")


#########################################################################
#
# Function name: access_input_file
# Input: name of inout file
# Output: Returns the content of the file.
#
# Purpose: determine the file type, extract the content and prepare for
#          URL extraction.
#
##########################################################################
def access_input_file(input_file):

    print ("\n***** access_input_file *****\n")

    ### Determine the file type ###
    extension = os.path.splitext(input_file.name)[1][1:]

    if extension in img_exts:
        ### Open image ###
        input_image = input_file
        screen_image = Image.open(input_image)

        ### Perform image-to-text conversion ###
        ### Python-tesseract is a wrapper for Google's Tesseract-OCR Engine.
        content = pytesseract.image_to_string(screen_image)
    elif extension in doc_exts:
        ### Read text content of csv file ###
        fp = open(input_file, 'r')
        content= fp.read()
        fp.close()

    else:
        print ("Unable to process blob.  File type has not been explicitly stated as part of the file name and/or is not supported.")
        content = None

    print ("Successfully accessed blob")

    return content


##########################################################################
#
# Function name: extract_URLs
# Input: content (text)
# Output: non-deduped, non-sorted list of extracted urls.
#
# Purpose: identify and extract the URLs present in the text input.
#
##########################################################################
def extract_URLs(content):

    if content is not None:
        print ("\n***** Extract URLs *****\n")
        ### Identify URLs in content ###
        extractor = URLExtract();
        urls  = extractor.find_urls(content)            # returns list of urls
        #iocs  = list(iocextract.extract_urls(content))  # another method for extracting urls

        print ("extractor.find method")
        print (urls)
        #print ("iocextract.extract_urls method")
        #print (iocs)

        info_to_evaluate = urls# + iocs

        index = 0

        # Occassionally, the functions above return urls with trailing commas.  Remove these.
        for url in info_to_evaluate:
            if url.endswith(','):
                info_to_evaluate[index] = url[:-1]
            index += 1

        print ("Removed trailing commas")
        print (info_to_evaluate)

        print ("Successfully extracted URLs")

        return info_to_evaluate


##########################################################################
#
# Function name: submit_URLs_Netcraft
# Input: Unique list of IPs
# Output: Returns the result of submitting the url(s) to netcraft:
#         - return_string: string describing success/failure of operation
#         - uuid: if call was successful, uuis should be non-zero.  Else, 
#           populated with "0000"
#         - state: the state of the request
#        
#
# Purpose: Submit list of unique URLs for processing with Netcraft.
#

##########################################################################
def submit_URLs_Netcraft(unique_url_list):

    print("\n***** Submit extracted URLs to Netcraft for evaluation *****\n")

    netcraftReport_url = "https://report.netcraft.com/api/v3/report/urls"

    headers = {'Content-type': 'application/json'}

    request_data = {
        "email": "smishing@cyber.gc.ca",
        "reason": "HMG",
        "urls": [{"url": u} for u in unique_url_list],
        }

    # Check URLs with netcraft service
    r_post = requests.post(netcraftReport_url, json=request_data, headers=headers)

    print("Netcraft Report URLs response status code: " + str(r_post.status_code))
    print(r_post.json())

    #state = {}
    uuid = "0000"

    # Update SQL db table entries where url is in unique_url_list with the uuid returned

    if r_post.status_code == 200:
        uuid = r_post.json()['uuid']
        print("UUID: " + str(uuid))
        return_string = "success"
    elif r_post.status_code == 400:
        # A number of different reasons could have caused this return code:
        # 1 - a single incorrectly formatted url
        # 2 - submission is an exact duplicate of a previous request
        # ... to add more as they come up ...
        response = r_post.json()
        #print(response)

        #print("**** ALL ****")
        #for all_url in unique_url_list:
        #    print(all_url)

        for field in response["details"]:
            print(field)
            if field["message"]:
                if "Duplicate" in field["message"]:
                    print("Duplicate Error")
                    return_string = "duplicate"

                if "Does not match url format" in field["message"]:
                    print("URL Formatting Error")
                    # Get the offending entries:
                    if field["input"]:
                        print(field["input"])
                        bad_url = field["input"]

                        print("Remove " + bad_url + " from list")
                        unique_url_list.remove(bad_url)

                    return_string = "formatting error"

                state = {}

        #print("**** GOOD ****")
        #for good_url in unique_url_list:
        #    print(good_url)

        if return_string == "formatting error" and len(unique_url_list) > 0:
            # resubmit the list with the poorly formatted URLs removed
            print("Resubmitting valid URLs")
            uuid = submit_URLs_Netcraft(unique_url_list)

    # Other possible error codes: 429 - too many submissions
    #else: 
    #    state = {}

    return uuid


##########################################################################
#
# Function name: update_cosmos_db
# Input: 
#    - list of uuids associated to report
#    - number of URLs reported via partner
#    - number of deduped URLs reported via partner
# Output: 
#
# Purpose: Add record of uuid.
#
##########################################################################
def update_cosmos_db(uuid, num_urls, url_list):
    
    print ("\n***** Add UUID to the COSMOS DB *****\n")
    uri              = os.environ.get('ACCOUNT_URI')
    key              = os.environ.get('ACCOUNT_KEY')
    database_id      = os.environ.get('DATABASE_ID')
    container_id = os.environ.get('CONTAINER_ID')

    #client = cosmos_client.CosmosClient(uri, {'masterKey': key})
    client = CosmosClient(uri, {'masterKey': key})

    database = client.get_database_client(database_id)
    container = database.get_container_client(container_id)

    # Get date
    date_str = datetime.today().strftime('%Y-%m-%d %H:%M:%S')
    all_uuid_str = uuid#' '.join(map(str, netcraft_uuids))

    #for uuid in netcraft_uuids:
    uuid_str = str(uuid)
    print (uuid_str)

    url_list_str   = ' '.join(map(str, url_list))

    print ("Informaton for new record: ")
    print ("    uuid: " + uuid_str)
    print ("    date: " + date_str)
    print ("    num URLs: " + str(num_urls))
    print ("    associated uuids: " + all_uuid_str)
    print ("    url list: " + url_list_str)

    # information to include:
    # - uuid
    # - associated uuids
    # - date
    # - number of unique URLs 
    # - number of valid URLs subitted to Netcraft
    # - list URLs received from client

    # statement to insert record
    container.upsert_item({ 'id': uuid_str,
                            'date': date_str,
                            'uuid': uuid_str,
                            'assoc_uuids': all_uuid_str,
                            'n_urls': num_urls,
                            'urls_str': url_list_str })

if __name__ == "__main__":
    main()

