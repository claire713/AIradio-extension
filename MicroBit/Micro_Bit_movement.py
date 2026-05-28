
"""
File: Micro_Bit_movement.py
Author: Claire Murphy
Date : May 2026
Description: This code runs on a micro:bit to detect movement using the accelerometer. 
It calculates the ENMO (Euclidean Norm Minus One) value from the accelerometer data and determines if the device is "still" or "moving" based on a threshold calculated through testing. 
The status is sent via Bluetooth UART to a connected device, and the micro:bit's LEDs are used to indicate the current status.

Developed using Microsoft MakeCode for BBC Micro:Bit.
NOTE : This is MakeCode Python - it uses MakeCode's built in APIs (basic, input, bluetooth) and cannot be run as standard Python.
It is displayed in this file so the code can be read
To modify: import the .hex file into https://makecode.microbit.org/

"""


# Constants
SAMPLE_PERIOD = 100
WINDOW_SAMPLES = 50
ENMO_THRESHOLD = 75

# State variables
connected = False
current_status = ""
new_status = ""
x = 0
y = 0
z = 0
vector_mag = 0
enmo = 0
enmo_sum = 0
window_count = 0
mean_enmo = 0


def on_bluetooth_connected():
    """
    This function is called when a Bluetooth connection is established. 
    It initializes the movement detection process by reading accelerometer data, calculating ENMO values, and determining the movement status every 5 seconds. 
    The status is sent via Bluetooth UART and displayed on the micro:bit's LEDs.
    """
    global connected, current_status, x, y, z, vector_mag, enmo, enmo_sum, window_count, mean_enmo, new_status
    connected = True
    current_status = "Na"
    basic.show_icon(IconNames.SMALL_SQUARE)   #display small sqaure when connected
    while connected:
        #Read X,Y and Z accelerometer values in mg  
        x = input.acceleration(Dimension.X)
        y = input.acceleration(Dimension.Y)
        z = input.acceleration(Dimension.Z)
        vector_mag = Math.sqrt(x * x + y * y + z * z)   # Vector magnitude
        enmo = vector_mag - 1000      #ENMO value in mg (Vector magnitufe - 1g)
        if enmo < 0:                 #truncate any resulting negative values to zero
            enmo = 0
        #Add ENMO value andd increment window count until number of samples for approximate 5 second window is reached
        enmo_sum += enmo
        window_count += 1
        if window_count >= WINDOW_SAMPLES:      
            mean_enmo = enmo_sum / WINDOW_SAMPLES   #calculate mean ENMO over sampling window
            new_status = "still"                  #default status is "still"
            if mean_enmo > ENMO_THRESHOLD:         
                new_status = "move"               #if mean ENMO exceeds threshold, update status to "move"
            if new_status != current_status:      #only send update if status has changed since last window
                current_status = new_status
                
                #Update LEDs and send status via Bluetooth UART
                if current_status == "move":
                    bluetooth.uart_write_line("active")
                    basic.show_leds("""
                        # # # # #
                        # # # # #
                        # # # # #
                        # # # # #
                        # # # # #
                        """)
                else:
                    bluetooth.uart_write_line("still")
                    basic.show_leds("""
                        . . . . .
                        . . . . .
                        # # # # #
                        . . . . .
                        . . . . .
                        """)
            #Reset for sampling window
            enmo_sum = 0
            window_count = 0
        basic.pause(SAMPLE_PERIOD)             #pause before next sample (100 ms) - approx 5 seconds between status updates (50 samples x 100 ms) not considering processing time
    basic.show_icon(IconNames.SMALL_DIAMOND)   #show small diamond when disconnected


def on_bluetooth_disconnected():
    """This function is called when the Bluetooth connection is lost.
        It resets the movement detection process and updates the micro:bit's LEDs to indicate that it is no longer connected.
    """
    global connected
    connected = False
    basic.show_icon(IconNames.SMALL_DIAMOND)

#Register Bluetooth event callbacks
bluetooth.on_bluetooth_connected(on_bluetooth_connected)
bluetooth.on_bluetooth_disconnected(on_bluetooth_disconnected)

bluetooth.start_uart_service()  #Start Bluetooth UART service