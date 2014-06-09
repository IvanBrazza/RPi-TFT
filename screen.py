# Last.fm/Pushbullet subscriber/Clock for Raspberry Pi
# This must run as root (sudo python screen.py) due to framebuffer, etc.
#
# http://www.adafruit.com/products/998  (Raspberry Pi Model B)
# http://www.adafruit.com/products/1601 (PiTFT Mini Kit)
#
# Prerequisite tutorials: aside from the basic Raspbian setup and PiTFT setup
# http://learn.adafruit.com/adafruit-pitft-28-inch-resistive-touchscreen-display-raspberry-pi
#
# screen.py by Ivan Brazza
# based on lapse.py by David Hunt (dave@davidhunt.ie), in turn
# based on cam.py by Phil Burgess / Paint Your Dragon for Adafruit Industries.
# BSD license, all text above must be included in any redistribution.

import fnmatch                # To load icons
import os                     # For various OS functions
import pygame                 # To create the UI
from pygame.locals import *   # For various pygame things
import threading              # Some functions need to run threaded
import time                   # For sleeping and the clock
import pylast                 # To connect to Last.fm
import json                   # To read/write preferences
import urllib                 # To fetch album covers
import RPi.GPIO as GPIO       # To access tac button presses
import websocket              # To connect to PB
import requests               # To fetch PB pushes
import base64

# UI classes ---------------------------------------------------------------

# Icon is a very simple bitmap class, just associates a name and a pygame
# image (PNG loaded from icons directory) for each.
# There isn't a globally-declared fixed list of Icons.  Instead, the list
# is populated at runtime from the contents of the 'icons' directory.

class Icon:
	def __init__(self, name):
	  self.name = name
	  try:
	    self.bitmap = pygame.image.load(iconPath + '/' + name + '.png')
	  except:
	    pass
# Button is a simple tappable screen region.  Each has:
#  - bounding rect ((X,Y,W,H) in pixels)
#  - optional background color and/or Icon (or None), always centered
#  - optional foreground Icon, always centered
#  - optional single callback function
#  - optional single value passed to callback
# Occasionally Buttons are used as a convenience for positioning Icons
# but the taps are ignored.  Stacking order is important; when Buttons
# overlap, lowest/first Button in list takes precedence when processing
# input, and highest/last Button is drawn atop prior Button(s).  This is
# used, for example, to center an Icon by creating a passive Button the
# width of the full screen, but with other buttons left or right that
# may take input precedence (e.g. the Effect labels & buttons).
# After Icons are loaded at runtime, a pass is made through the global
# buttons[] list to assign the Icon objects (from names) to each Button.

class Button:
	def __init__(self, rect, **kwargs):
	  self.rect     = rect # Bounds
	  self.color    = None # Background fill color, if any
	  self.iconBg   = None # Background Icon (atop color fill)
	  self.iconFg   = None # Foreground Icon (atop background)
	  self.bg       = None # Background Icon name
	  self.fg       = None # Foreground Icon name
	  self.callback = None # Callback function
	  self.value    = None # Value passed to callback
	  for key, value in kwargs.iteritems():
	    if   key == 'color': self.color    = value
	    elif key == 'bg'   : self.bg       = value
	    elif key == 'fg'   : self.fg       = value
	    elif key == 'cb'   : self.callback = value
	    elif key == 'value': self.value    = value

	def selected(self, pos):
	  x1 = self.rect[0]
	  y1 = self.rect[1]
	  x2 = x1 + self.rect[2] - 1
	  y2 = y1 + self.rect[3] - 1
	  if ((pos[0] >= x1) and (pos[0] <= x2) and
	      (pos[1] >= y1) and (pos[1] <= y2)):
	    if self.callback:
	      if self.value is None: self.callback()
	      else:                  self.callback(self.value)
	    return True
	  return False

	def draw(self, screen):
	  if self.color:
	    screen.fill(self.color, self.rect)
	  if self.iconBg:
	    screen.blit(self.iconBg.bitmap,
	      (self.rect[0]+(self.rect[2]-self.iconBg.bitmap.get_width())/2,
	       self.rect[1]+(self.rect[3]-self.iconBg.bitmap.get_height())/2))
	  if self.iconFg:
	    screen.blit(self.iconFg.bitmap,
	      (self.rect[0]+(self.rect[2]-self.iconFg.bitmap.get_width())/2,
	       self.rect[1]+(self.rect[3]-self.iconFg.bitmap.get_height())/2))

	def setBg(self, name):
	  if name is None:
	    self.iconBg = None
	  else:
	    for i in icons:
	      if name == i.name:
	        self.iconBg = i
	        break

class Backlight:
  def __init__(self, config):
    os.system("echo 252 > /sys/class/gpio/export")
    os.system("echo 'out' > /sys/class/gpio/gpio252/direction")
    if config['settings']['backlight'] == "on":
      self.on()
    elif config['settings']['backlight'] == "off":
      self.off()

  def on(self):
    os.system("echo '1' > /sys/class/gpio/gpio252/value")
    config['settings']['backlight'] = "on"
  
  def off(self):
    os.system("echo '0' > /sys/class/gpio/gpio252/value")
    config['settings']['backlight'] = "off"

# UI callbacks -------------------------------------------------------------
# These are defined before globals because they're referenced by items in
# the global buttons[] list.

def testCallback():
  log("test", "INFO")

def screenCallback(n): # Switch to a screen mode
  global screenMode
  if n is 5:
    global config, numberstring
    numberstring = str(config['settings']['timeout'])
  screenMode = n

def clockCallback(): # Enable backlight if off, show settings if on
  global screenMode, backlight, config, sleep
  if config['settings']['backlight'] == "on":
    screenMode = 1
  elif config['settings']['backlight'] == "off":
    backlight.on()
    sleep = 0

def nowPlayingCallback(): # Enable/disable backlight
  global backlight, config, sleep
  if config['settings']['backlight'] == "on":
    backlight.off()
  elif config['settings']['backlight'] == "off":
    backlight.on()
    sleep = 0

def mainCallback(): # Exit settings
	global screenMode
	screenMode = 0 # Switch back to main window

def backlightCallback(): # Enable/disable backlight and save to config
  global config, backlight
  if config['settings']['backlight'] == "on":
    backlight.off()
    config['settings']['backlight'] = "off"
  elif config['settings']['backlight'] == "off":
    backlight.on()
    config['settings']['backlight'] = "on"
  saveConfig()

def timeoutCallback(n): # Numerical input for timeout
  global screenMode, numberstring, config
  if n < 10:
    numberstring = numberstring + str(n)
  elif n == 10:
    numberstring = numberstring[:-1]
  elif n == 11:
    screenMode = 1
  elif n == 12:
    screenMode = 1
    config['settings']['timeout'] = numberstring
    saveConfig()

def mirroringCallback():
  global config
  if config['pushbullet']['mirroring'] == "on":
    config['pushbullet']['mirroring'] = "off"
  elif config['pushbullet']['mirroring'] == "off":
    config['pushbullet']['mirroring'] = "on"
  saveConfig()

# Global stuff -------------------------------------------------------------
screenMode      =  0      # Current screen mode; default = viewfinder
screenModePrior = -1      # Prior screen mode (for detecting changes)
iconPath        = 'icons' # Subdirectory containing UI bitmaps (PNG format)
sleep           = 0       # Seconds counter for backlight timeout
icons           = []      # This list gets populated at startup
numberstring    = "0"     # Backlight timer numerical input

# buttons[] is a list of lists; each top-level list element corresponds
# to one screen mode, and each element within those lists corresponds to one UI button.

buttons = [

  # 0 - Clock
  [Button((0, 0, 320, 240), cb=clockCallback)],

  # 1 - Settings
  [Button((260,  0, 60, 60), bg='cog',   cb=backlightCallback),
   Button((260, 60, 60, 60), bg='cog',   cb=screenCallback, value=5),
   Button((260,120, 60, 60), bg='cog',   cb=mirroringCallback),
   Button((  0,180,160, 60), bg='ok',    cb=mainCallback),
   Button((160,180, 70, 60), bg='left',  cb=testCallback),
   Button((230,180, 70, 60), bg='right', cb=testCallback)],

  # 2 - Now Playing
  [Button((  0,   0, 320, 180),           cb=nowPlayingCallback),
   Button((130, 180,  60,  60), bg='cog', cb=screenCallback, value=3)],

  # 3 - Track info
  [Button(( 90, 180, 140,  60), bg='ok', cb=screenCallback, value=2)],

  # 4 - Pushbullet push
  [],

  # 5 - Backlight timeout numerical input
  [Button((  0,  0,320, 60), bg='box'),
   Button((180,120, 60, 60), bg='0',     cb=timeoutCallback, value=0),
   Button((  0,180, 60, 60), bg='1',     cb=timeoutCallback, value=1),
   Button((120,180, 60, 60), bg='3',     cb=timeoutCallback, value=3),
   Button(( 60,180, 60, 60), bg='2',     cb=timeoutCallback, value=2),
   Button((  0,120, 60, 60), bg='4',     cb=timeoutCallback, value=4),
   Button(( 60,120, 60, 60), bg='5',     cb=timeoutCallback, value=5),
   Button((120,120, 60, 60), bg='6',     cb=timeoutCallback, value=6),
   Button((  0, 60, 60, 60), bg='7',     cb=timeoutCallback, value=7),
   Button(( 60, 60, 60, 60), bg='8',     cb=timeoutCallback, value=8),
   Button((120, 60, 60, 60), bg='9',     cb=timeoutCallback, value=9),
   Button((240,120, 80, 60), bg='del',   cb=timeoutCallback, value=10),
   Button((180,180,140, 60), bg='ok',    cb=timeoutCallback, value=12),
   Button((180, 60,140, 60), bg='cancel',cb=timeoutCallback, value=11)],

  # 6 - Pushbullet notification mirror
  []
]


# Assorted utility functions -----------------------------------------------
def TFTBtn2Click(channel):
  pygame.event.post(click2event)

# Run on a separate thread, CheckInputs() never stops checking touchscreen
# and tac switch input, sleeping for 0.8s at the end of each loop to reduce
# CPU usage.
def CheckInputs():
  global config
  while True:
    for event in pygame.event.get():
      if event.type is MOUSEBUTTONDOWN:
        pos = pygame.mouse.get_pos()
        for b in buttons[screenMode]:
          if b.selected(pos): break

      if event.type is TFTBUTTONCLICK:
        if event.button is 2:
          if config['settings']['backlight'] == "on":
            backlight.off()
            config['settings']['backlight'] = "off"
          elif config['settings']['backlight'] == "off":
            backlight.on()
            config['settings']['backlight'] = "on"
          saveConfig()
    time.sleep(0.8)

# Collect user input to create a fresh config file
def CreateConfig():
  global config

  log("A configuration file was not found. Let's create one.", "WARN")
  LAST_KEY        = None
  LAST_SECRET     = None
  last_username   = None
  last_password   = None
  push_key        = None
  push_mirroring  = None

  #get config details
  while not LAST_KEY:
    LAST_KEY       = raw_input("Enter your Last.fm API key: ")
  while not LAST_SECRET:
    LAST_SECRET    = raw_input("Enter your Last.fm API secret: ")
  while not last_username:
    last_username  = raw_input("Enter your Last.fm username: ")
  while not last_password:
    last_password  = pylast.md5(raw_input("Enter your Last.fm password: "))
  print "Pushbullet features are optional. Leave these blank if you don't want to use it."
  push_key       = raw_input("Enter your Pushbullet API key (can be found at https://www.pushbullet.com/account): ")
  if push_key:
    while True:
      push_mirroring = raw_input("Would you like to enable Pushbullet notifictation mirroring? (must be enabled on device) [on/off] ").lower()
      if push_mirroring != "on" and push_mirroring != "off":
        print "Please enter on/off"
      else:
        break

  config = {
    'lastfm': {
      'API_KEY':     LAST_KEY,
      'API_SECRET':  LAST_SECRET,
      'username':    last_username,
      'password':    last_password
    },
    'pushbullet': {
      'API_KEY':     push_key,
      'mirroring':   push_mirroring
    },
    'settings': {
      'backlight':   "on",
      'timeout':     "9"
    }
  }
  with open('config.json', 'w') as outfile:
    json.dump(config, outfile, indent=2)

# Write the current configuration and preferences (which should
# always be up to date in config) to config.json
def saveConfig():
  towrite   = {
    'lastfm': {
      'API_KEY':     config['lastfm']['API_KEY'],
      'API_SECRET':  config['lastfm']['API_SECRET'],
      'username':    config['lastfm']['username'],
      'password':    config['lastfm']['password']
    },
    'pushbullet': {
      'API_KEY':     config['pushbullet']['API_KEY'],
      'mirroring':   config['pushbullet']['mirroring']
    },
    'settings': {
      'backlight':   config['settings']['backlight'],
      'timeout':     config['settings']['timeout']
    }
  }
  with open('config.json', 'w') as outfile:
    json.dump(towrite, outfile, indent=2)

# Log a message with a timestamp to the console window
def log(logmsg, type):
  timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
  print "[{0}] [{1}] {2}".format(timestamp, type, logmsg)

# Run on a separate thread, InitPB() connects to the Pushbullet
# websocket, and keeps the connection open forever
def InitPB():
  websocket.enableTrace(False)
  ws = websocket.WebSocketApp("wss://stream.pushbullet.com/websocket/" + config['pushbullet']['API_KEY'], on_message = OnPBMessage)
  ws.on_open = OnPBStart
  ws.run_forever()

# When a connection to the Pushbullet websocket is established,
# log and get all available Pushbullet devices
def OnPBStart(ws):
  global PbDevices
  log("Connected to Pushbullet WebSocket", "INFO")
  r = requests.get("https://api.pushbullet.com/v2/devices", auth=(config['pushbullet']['API_KEY'], ''))
  PbDevices = r.json()['devices']

# Whenever something happens in the Pushbullet websocket.
def OnPBMessage(ws, message):
  global PbMessage, screenMode, PbPrior, config
  timestamp = time.time()
  message   = json.loads(message)
  if message['type'] == "tickle": # A new push was sent, fetch it!
    r = requests.get("https://api.pushbullet.com/v2/pushes?modified_after=" + str(time.time() - 100), auth=(config['pushbullet']['API_KEY'], ''))
    PbMessage = r.json()
    if PbMessage['pushes'][0]['type']:
      PbPrior = screenMode
      screenMode = 4
  elif message['type'] == "push" and config['pushbullet']['mirroring'] == "on": # A notification happened somewhere, show it if enabled
    imgdata = base64.b64decode(message['push']['icon']) # The notification icon is encoded in base64, decode it
    with open("cache/pb-mirror.png", "wb") as f:
      f.write(imgdata)
    PbMessage = message['push']
    PbPrior = screenMode
    screenMode = 6

# Initialization -----------------------------------------------------------

# Init framebuffer/touchscreen environment variables
os.putenv('SDL_VIDEODRIVER', 'fbcon')
os.putenv('SDL_FBDEV'      , '/dev/fb1')
os.putenv('SDL_MOUSEDRV'   , 'TSLIB')
os.putenv('SDL_MOUSEDEV'   , '/dev/input/touchscreen')


# Init pygame and screen
log("Initting...", "INFO")
pygame.init()
log("Setting mouse invisible...", "INFO")
pygame.mouse.set_visible(False)
log("Setting fullscreen...", "INFO")
modes = pygame.display.list_modes(16)
screen = pygame.display.set_mode(modes[0], FULLSCREEN, 16)

log("Loading icons...", "INFO")
# Load all icons at startup.
for file in os.listdir(iconPath):
  if fnmatch.fnmatch(file, '*.png'):
    icons.append(Icon(file.split('.')[0]))
# Assign Icons to Buttons, now that they're loaded
log("Assigning buttons...", "INFO")
for s in buttons:        # For each screenful of buttons...
  for b in s:            #  For each button on screen...
    for i in icons:      #   For each icon...
      if b.bg == i.name: #    Compare names; match?
        b.iconBg = i     #     Assign Icon to Button
        b.bg     = None  #     Name no longer used; allow garbage collection
      if b.fg == i.name:
        b.iconFg = i
        b.fg     = None

# Check config
if os.path.isfile('config.json'):
  with open('config.json') as infile:
    config = json.load(infile)
else:
  CreateConfig()

# Init pylast
log("Connecting to Last.fm...", "INFO")
API_KEY     = config['lastfm']['API_KEY']
API_SECRET  = config['lastfm']['API_SECRET']
username    = config['lastfm']['username']
password    = config['lastfm']['password']
network     = pylast.LastFMNetwork(api_key = API_KEY, api_secret = API_SECRET, username = username, password_hash = password)
user        = network.get_user("dudeman1996")
result      = user.get_now_playing()
if result: # If scrobbling, get details
  screenMode = 2
  artist = result.artist.get_name()
  try:
    album = result.get_album().get_name()
  except:
    album = " "
  title = result.get_title()
  try:
    cover = result.get_album().get_cover_image(size=2)
  except:
    cover = False
else:
  title = " "

# Set the second tact switch up
GPIO.setmode(GPIO.BCM)
TFTBUTTONCLICK = pygame.USEREVENT + 1
click2event = pygame.event.Event(TFTBUTTONCLICK, button=2)
GPIO.setup(22, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.add_event_detect(22, GPIO.FALLING, callback=TFTBtn2Click, bouncetime=200)

# Init the Backlight class
backlight = Backlight(config)

# Display a blank screen
screen.fill(0)
pygame.display.update()

# Start the CheckInputs thread
inputThread = threading.Thread(target=CheckInputs)
inputThread.setDaemon(True)
inputThread.start()

# Start the Pushbullet websocket thread
PbThread = threading.Thread(target=InitPB)
PbThread.setDaemon(True)
PbThread.start()

# Main loop ----------------------------------------------------------------
log("Begin.", "INFO")
while(True):
  screen.fill(0)

  # Overlay buttons on display and update
  for i,b in enumerate(buttons[screenMode]):
    b.draw(screen)

  if screenMode is 6: # PB notification mirro
    backlight.on()
    myfont  = pygame.font.SysFont("Arial", 20)

    lastimg = pygame.image.load("icons/pb.png")
    screen.blit(lastimg, (20, 8))

    label           = myfont.render("New Notification!", 1, (255,255,255))
    textpos         = label.get_rect()
    textpos.centerx = screen.get_rect().centerx
    textpos.centery = 20
    screen.blit(label, textpos)

    mirror = pygame.image.load("cache/pb-mirror.png")
    mirrorscaled = pygame.transform.scale(mirror, (72, 72))
    screen.blit(mirrorscaled, (20, 84))

    label = myfont.render(PbMessage['title'], 1, (255,255,255))
    screen.blit(label, (102, 110))

    pygame.display.update()
    screenMode = PbPrior
    sleep = 0
    time.sleep(7)
    screen.fill(0)

  if screenMode is 5: # Numeric input
    myfont = pygame.font.SysFont("Arial", 50)
    label = myfont.render(numberstring, 1, (255,255,255))
    screen.blit(label, (10, 2))

  if screenMode is 4: # PB message
    backlight.on()
    push    = PbMessage['pushes'][0]
    myfont  = pygame.font.SysFont("Arial", 20)
    y       = 100

    if 'target_device_iden' in push:
      for device in PbDevices:
        if device['iden'] == push['target_device_iden']:
          if 'nickname' in device:
            to = device['nickname']
            break
          elif 'model' in device:
            to = device['model']
            break

    label           = myfont.render("New Push!", 1, (255,255,255))
    textpos         = label.get_rect()
    textpos.centerx = screen.get_rect().centerx
    textpos.centery = 20
    screen.blit(label, textpos)

    lastimg = pygame.image.load("icons/pb.png")
    screen.blit(lastimg, (20, 8))

    label           = myfont.render("New " + push['type'] + " from " + push['sender_email_normalized'], 1, (255,255,255))
    textpos         = label.get_rect()
    textpos.centerx = screen.get_rect().centerx
    textpos.centery = 60
    screen.blit(label, textpos)

    if to:
      label           = myfont.render("(To " + to + ")", 1, (255,255,255))
      textpos         = label.get_rect()
      textpos.centerx = screen.get_rect().centerx
      textpos.centery = 80
      screen.blit(label, textpos)

    if 'title' in push:
      label = myfont.render(push['title'], 1, (255,255,255))
      screen.blit(label, (20, y))
      y += 20

    if 'body' in push:
      label = myfont.render(push['body'], 1, (255,255,255))
      screen.blit(label, (20, y))
      y += 20

    if push['type'] == "link":
      label = myfont.render(push['url'], 1, (255,255,255))
      screen.blit(label, (20, y))
      y += 20

    pygame.display.update()
    screenMode = PbPrior
    sleep = 0
    time.sleep(7)
    screen.fill(0)

  if screenMode is 3: # Track info
    bg = pygame.image.load("icons/nowplaying.png")
    screen.blit(bg, (0,0))

    lastimg = pygame.image.load("icons/lastfm.png")
    screen.blit(lastimg, (19, 8))
    
    myfont = pygame.font.SysFont("Arial", 20)

    label           = myfont.render("Track Info", 1, (255,255,255))
    textpos         = label.get_rect()
    textpos.centerx = screen.get_rect().centerx
    textpos.centery = 20
    screen.blit(label, textpos)

  if screenMode is 2: # Now Playing
    bg = pygame.image.load("icons/nowplaying.png")
    screen.blit(bg, (0,0))

    lastimg = pygame.image.load("icons/lastfm.png")
    screen.blit(lastimg, (19, 8))
    
    myfont = pygame.font.SysFont("Arial", 20)

    if cover:
      urllib.urlretrieve(cover, "cache/cover.png")
      coverimg = pygame.image.load("cache/cover.png")
      coverscaled = pygame.transform.scale(coverimg, (115, 115))
      screen.blit(coverscaled, (19, 48))

    label = myfont.render("Now Scrobbling", 1, (255,255,255))
    textpos         = label.get_rect()
    textpos.centerx = screen.get_rect().centerx
    textpos.centery = 20
    screen.blit(label, textpos)

    label = myfont.render(artist, 1, (255,255,255))
    screen.blit(label, (145, 72))

    label = myfont.render(album, 1, (255,255,255))
    screen.blit(label, (145, 102))

    label = myfont.render(title, 1, (255,255,255))
    screen.blit(label, (145, 132))

  if screenMode is 1: # Settings
    sleep = 0
    myfont = pygame.font.SysFont("Arial", 30)
    label = myfont.render("Backlight:" , 1, (255,255,255))
    screen.blit(label, (10, 10))
    label = myfont.render("Timeout:" , 1, (255,255,255))
    screen.blit(label, (10, 70))
    label = myfont.render("Mirroring:" , 1, (255,255,255))
    screen.blit(label, (10,130))

    label = myfont.render(config['settings']['backlight'], 1, (255,255,255))
    screen.blit(label, (130, 10))
    label = myfont.render(str(config['settings']['timeout']) + " seconds", 1, (255,255,255))
    screen.blit(label, (130, 70))
    label = myfont.render(str(config['pushbullet']['mirroring']) , 1, (255,255,255))
    screen.blit(label, (130,130))

  if screenMode is 0: # Clock
    mytime          = time.strftime("%H:%M")
    myp             = time.strftime("%p")
    clockfont       = pygame.font.Font("SFDigitalReadout-Medium.ttf", 120)
    pfont           = pygame.font.SysFont("Arial", 30)
    clocklabel      = clockfont.render(mytime, 1, [255,255,255])
    plabel          = pfont.render(myp, 1, [255,255,255])
    textpos         = clocklabel.get_rect()
    textpos.centerx = screen.get_rect().centerx
    textpos.centery = screen.get_rect().centery
    ppos            = [ textpos[0] + textpos[2] + 10, textpos[1] ]
    screen.blit(plabel, ppos)
    screen.blit(clocklabel, textpos) 

  # Update the screen
  pygame.display.update()
  screenModePrior = screenMode

  # Check if anything is scrobbling
  try:
    result = user.get_now_playing()
  except:
    log("Failed to get now playing", "ERROR")
  if result and screenMode != 3:
    if title != result.get_title():
      sleep = 0
      backlight.on()
      artist = result.artist.get_name()
      try:
        album = result.get_album().get_name()
      except:
        album = " "
      title = result.get_title()
      try:
        cover = result.get_album().get_cover_image(size=2)
      except:
        cover = False
    screenMode = 2
  elif screenMode == 0 or screenMode == 2:
    if screenModePrior != 0:
      backlight.on()
      sleep = 0
    screenMode = 0

  # Sleep (turn off the backlight) after x seconds defined in the config
  if sleep < int(config['settings']['timeout']):
    sleep += 1
  if sleep >= int(config['settings']['timeout']):
    if screenMode == 0 or screenMode == 2: # Only sleep on the Clock or Now Playing screens
      backlight.off()
  time.sleep(1)
