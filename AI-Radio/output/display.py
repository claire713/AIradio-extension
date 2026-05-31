import sys
import os
picdir = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), 'pic')
libdir = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), 'lib')
if os.path.exists(libdir):
   sys.path.append(libdir)

import logging
from epd5in79 import EPD


import time
from PIL import Image,ImageDraw,ImageFont
import traceback
import requests
from io import BytesIO


logging.basicConfig(level=logging.DEBUG)

try:
   logging.info("E-ink Vradio Application")
   fname  =  ""
   if len(sys.argv) > 1:
       fname = sys.argv[1]   # first argument after script name
       print(f"You passed: {fname }")
   else:
       print("No argument provided")
       
       exit()

   epd = EPD()
   logging.info("init and Clear")
   epd.init_Fast()
   #epd.Clear()
   #time.sleep(1)
    
   # Drawing on the Horizontal image
   logging.info("Loading image...")
   if "https" in fname:

       response = requests.get(fname)
       response.raise_for_status()  # good practice to catch errors

       Limage = Image.open(BytesIO(response.content))

       Limage = Image.open(fname)

   else:
       Limage = Image.open(fname)
   #draw = ImageDraw.Draw(Limage)
   #draw.rectangle([0, 0, 100, 100], outline="white", width=3)  # outline only

   epd.display_Fast(epd.getbuffer(Limage))
   #time.sleep(1)

    
   epd.sleep()
        
except IOError as e:
   logging.info(e)
    
except KeyboardInterrupt:     
   logging.info("ctrl + c:")
   epd5in83b_V2.epdconfig.module_exit(cleanup=True)
   exit()