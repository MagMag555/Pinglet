# Pinglet
An Open-Source Website Pinging Service for Debugging! More comming soon ;)

IMPORTANT: Pinglet is a service for debugging sites, if you are using our program for harmful actions, it's totally on your own and we do not take any charge of that!

##Files

Logo_Pinglet.png -- the original PNG of icon_pinglet.ico
icon_pinglet.ico -- the icon file for the program
main.py -- the source code used for building the exe
Pinglet.msi -- the isntaller file for the exe
pinglet.wixdpb -- needed for the build of Pinglet.msi
Package.wxs -- the package file for wix, used for building the msi file
README.md -- this right here

##Building by yourself 

1. Install Python on your Windows PC.

2. Open a terminal in the project directory.

3. Install the required dependencies:
   python -m pip install customtkinter pyinstaller

4. Make sure main.py and icon_pinglet.ico are in the project directory.

5. Build Pinglet using PyInstaller:
   python -m PyInstaller --noconfirm --clean --windowed --onefile --name Pinglet --icon=icon_pinglet.ico main.py

6. Once the build is complete, you can find Pinglet.exe in the dist folder.

**Note**: If Pinglet requires additional libraries, install them before building. Windows security features may block unsigned executables.

There's a OpenSource License for the msi's exe file to prevent blockage by Smart App Control, note that if you build yourself you have to add one manually

##General Information

The exe file is 41MB large, because of that it isn't listed here
**Note:** The exe file may take up to 30 seconds to load, we are working on that one
