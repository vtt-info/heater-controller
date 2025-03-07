import utime
import sys
from machine import ADC, Pin, I2C, Timer, WDT, PWM
from ssd1306 import SSD1306_I2C

from simple_pid import PID

from errormessage import ErrorMessage
from customtimer import CustomTimer
from thermocouple import Thermocouple
from displaymanager import DisplayManager
from inputhandler import InputHandler
from menusystem import MenuSystem

from heaters import HeaterFactory, InductionHeater, ElementHeater


#pid_tunings = 0.48, 0.004, 0   #18mm + nichrome 2mm
#pid_tunings = 0.29, 0.0008, 0   #18mm + nichrome 3mm - 60% limit
#pid_tunings = 0.33, 0.0011, 0   #20mm + nichrome 3mm - 70% limit

#pid_tunings = 0.27, 0.00065, 0   #new heater + 6 coil + nichrome 4mm approx 0.7 ohms - 40% pwm limit - 73 watts meeasured 



#pid_tunings = 0.27, 0.00065, 0   #new heater + 6 coil + nichrome 4mm approx 0.7 ohms - 30% pwm limit - 57 watts meeasured 
#pid_tunings = 0.28, 0.0008, 0   #new heater + 6 coil + nichrome 4mm approx 0.7 ohms - 25% pwm limit - 47 watts meeasured 


pid_tunings = 0.21, 0.002, 0   #new heater + 6 coil + nichrome 4mm approx 0.55 ohms - with 2 x lipo batteries


# Limit max_duty_cycle_percent - use this if you need to protect power supply/batteries 
# eg: max power supply watts: 120W - 12v @ 10A Max
#     If we know the element will pull 200W (from resitance of it and supply voltage)
#     need to limit pwm cyle to 120/200 * 100 = 60% 
#
# After changing this the pid tunings may need to be updated

#heater_max_duty_cycle_percent = 55  #set to 100 for no limit  
#moved to shaed sate

#add option for PWM mode so dial sets duty %  and ignore pid/temp (up to 300?) and just go in manual mode - show watts as we can work it out

#Need to get input voltage measured so we can possibly set an upper limit 
#eg:
#24v 0.6ohm 40amp 960w  5%-8%  (50-80w)
#12v 0.6ohm 20amp 240w  25-33% (60-80w)
# 9v 0.6ohm 15amp 135w  45-60% (60-80w)
# 6v 0.6ohm 10amp  60w  100%   (60w)


#Note if we can get input voltage for coil then we can possibly set some sensible default for heater_max_duty_cycle_percent
#also choose the correct profile automatically - ie know its battery or mains - get user to confirm  
# - ie to then enable/diable battery check and also et preset pid values for each battery setup type or mains from profile
#add new graphs:
# voltage over time 
# watts over time - should be able to work this out if we get the resitance as a constant and know the voltage - if we know the duty cylcle we should be able to work out the watts 
# show watts use on display home screen? compare to power meter to see if its correct

#quick heat function - if watts not too high maybe allow boost to speed up intial heat from cold (and temp under 100) and use up to 100W for 10-15 secs?

#if adding new button add maybe if in session and down to under 1 min if press it adds a minute/extra time to session.

hardware_pin_led = 25 #machine.Pin # default led on the pico could change to a different led on a pin if wanted eg for external housing

hardware_pin_display_scl = 21
hardware_pin_display_sda = 20

hardware_pin_buzzer = 16

hardware_pin_rotary_clk = 13
hardware_pin_rotary_dt = 12
hardware_pin_button = 14  #can also be a separate button as well as rotary push/sw pin - just wire both switches to pin and ground

hardware_pin_termocouple_sck = 6
hardware_pin_termocouple_cs = 7 
hardware_pin_termocouple_so = 8

hardware_pin_heater = 22

# need to add two pins for buttons for up/down left/right so we can do navigation/changes without rotary dial if wanted (use rotary switch as fire/ok still pin 14)


####################################

# Format:  main_system-error_code

MAIN_ERROR_MESSAGES = {"display-setup":      "Error initializing display, cannout continue",
                       "heater-too_hot":     "Heater too hot > 300C",
                       "pi-too_hot":         "PI too hot > 60C"
}

def load_config(file_path='config.txt'):
    config = {}
    try:
        with open(file_path, 'r') as file:
            for line in file:
                if line.strip() and not line.startswith('#'): # Ignore empty lines and comments
                    key, value = line.strip().split('=')
                    if key == 'session_timeout':
                        config['session_timeout'] = int(value) * 1000 # Convert to milliseconds
                    elif key == 'temperature_units':
                        config['temperature_units'] = value
                    elif key == 'setpoint':
                        config['setpoint'] = int(value)
                    elif key == 'power_threshold':
                        config['power_threshold'] = int(value)
                    elif key == 'heater_on_temperature_difference_threshold':
                        config['heater_on_temperature_difference_threshold'] = int(value)
                    # Add more elif statements for other configuration settings
    except OSError as e:
        print("Error opening or reading config file:", e)
    return config


def get_pi_temperature_or_handle_error(pi_temperature_sensor):
    try:
        ADC_voltage = pi_temperature_sensor.read_u16() * (3.3 / (65536))
        pi_temperature = 27 - (ADC_voltage - 0.706) / 0.001721
        return pi_temperature
    except Exception as e:
        error_message = str(e)
        print("Error reading PI temperature: " + error_message)
        display_manager.display_error("pi-unknown_error", "Error reading PI temperature: " + error_message,10,True) # need to move out of this?
        #while True:
         #   utime.sleep_ms(1000)
    return pi_temperature

def get_thermocouple_temperature_or_handle_error(thermocouple, heater):
    try:

        if isinstance(heater, InductionHeater):
            new_temperature, need_off_temperature = thermocouple.get_filtered_temp(heater.is_on())
        elif isinstance(heater, ElementHeater):
            new_temperature = thermocouple.read_raw_temp()
            need_off_temperature = False  # caller can throw this away if not needed
        else:
            raise ValueError("Unsupported heater type")
        return new_temperature, need_off_temperature
    
    except ErrorMessage as e:
        error_message = str(e)
        error_code = e.error_code
        if error_code in ["thermocouple-invalid_reading",
                          "thermocouple-zero_reading", 
                          "thermocouple-below_zero"]:
            heater.off()
            if pidTimer.is_timer_running(): pidTimer.stop() # Maybe stop other timers?
            print("Stopped heater - [" + error_code + "] " + error_message)
            while True:
                display_manager.display_error(error_code, "Stopped heater - " + error_message)  # need to move out of this?
                utime.sleep_ms(500)
        else:
            #thermocouple-above_limit, thermocouple-read_error
            heater.off()
            
            print("Pausing heater - [" + error_code + "] " + error_message)
            #display_manager.display_error(error_code, "Pausing heater - " + error_message,10,True)  # need to move out of this?
                                                                                                 

            return -1, True
    
    except Exception as e:
        # Handle or log unexpected exceptions not dealt with above
        error_message = str(e)
        heater.off()
        if pidTimer.is_timer_running(): pidTimer.stop() 
        print("Stopped heater - Unknown Error: " + error_message)
        while True:
            display_manager.display_error("unknown_error","Stopped heater - Unknown Error: " + error_message)
            utime.sleep_ms(500)

def initialize_display(i2c_scl, i2c_sda, led_pin):
 
    try:
        i2c = I2C(0, scl=Pin(i2c_scl), sda=Pin(i2c_sda), freq=200000)
        display = SSD1306_I2C(128, 32, i2c)
    except Exception as e:
        error_text = "Start up failed - [display-setup] " + MAIN_ERROR_MESSAGES["display-setup"] + " " + str(e)
        print(error_text)
        while True:
            # We could so a special lookup for each error type for the display and morse code it out?
            # For time being 3 on/off in short time with a pause and then repeating is enough to notify 
            # about a display issue
            led_pin.on()
            utime.sleep_ms(200)
            led_pin.off()
            utime.sleep_ms(200)
            led_pin.on()
            utime.sleep_ms(200)
            led_pin.off()
            utime.sleep_ms(200)
            led_pin.on()
            utime.sleep_ms(200)
            led_pin.off()
            utime.sleep_ms(1000)
        sys.exit()

    return display



def timerSetPiTemp(t):
    global pi_temperature_sensor, pidTimer, display_manager, heater, shared_state
   
    shared_state.pi_temperature = get_pi_temperature_or_handle_error(pi_temperature_sensor)
    
    # Check if the temperature is safe
    if shared_state.pi_temperature > shared_state.pi_temperature_limit:
        try:
            if not pidTimer.is_timer_running: pidTimer.stop() 
            heater.off()
            while not shared_state.pi_temperature <= shared_state.pi_temperature_limit:
                display_manager.display_error("pi-too_hot", MAIN_ERROR_MESSAGES["pi-too_hot"] + " " + str(pi_temperature) + "C", 5) # Move display out of here?
                
                shared_state.pi_temperature = get_pi_temperature_or_handle_error(pi_temperature_sensor)
                utime.sleep_ms(250)  # Warning shown for 5 secs so has had a time to cool down a bit

            pidTimer.start()
        except Exception as e:
            heater.off()
            print("Error updating display or deinitializing timers:", e)
            # dont feed watchdog let it reboot
    else:
        if not pidTimer.is_timer_running: pidTimer.start()


def timerUpdatePIDandHeater(t):  #nmay replace what this does in the check termocouple function 
                                 #this needs a major clear up now we have share_state 
    global pid, heater, thermocouple

    if pid.setpoint != shared_state.setpoint: pid.setpoint = shared_state.setpoint
    
    new_heater_temperature, need_heater_off_temperature = get_thermocouple_temperature_or_handle_error(thermocouple, heater)

    if new_heater_temperature < 0: # Non fatal error occured 
        heater.off() #should already be off
        return   # Let timer run this again and hopefully next time error has passed

    # new temperature is valid
    shared_state.heater_temperature = new_heater_temperature
    
    if need_heater_off_temperature:
        heater.off()
        print("Getting safe off heater temperature")
        utime.sleep_ms(301) # lets give everything a moment to calm down
        new_heater_temperature, _ = get_thermocouple_temperature_or_handle_error(thermocouple, heater)
        if new_heater_temperature < 0: # Non fatal error occured 
            return   # Let timer run this again and hopefully next time error has passed
        # new off temperature is valid
        shared_state.heater_temperature = new_heater_temperature

    if len(shared_state.temperature_readings) >= 128: 
        oldest_time = min(shared_state.temperature_readings.keys())
        del shared_state.temperature_readings[oldest_time]
    shared_state.temperature_readings[utime.ticks_ms()] = int(shared_state.heater_temperature)

    if len(shared_state.watt_readings) >= 128: 
        oldest_time = min(shared_state.watt_readings.keys())
        del shared_state.watt_readings[oldest_time]
    
    if heater.is_on():
        shared_state.watts = int((((shared_state.input_volts*shared_state.input_volts) / shared_state.heater_resitance) * (shared_state.heater_max_duty_cycle_percent/100))  * (heater.get_power() / 10))
        shared_state.watt_readings[utime.ticks_ms()] = shared_state.watts
    else:
        shared_state.watts = 0
        shared_state.watt_readings[utime.ticks_ms()] = 0


    power = pid(shared_state.heater_temperature)  # Update pid even if heater is off

    if shared_state.get_mode() == "Off": 
        heater.off()
        return
    
    if power > shared_state.power_threshold:
        if abs(shared_state.heater_temperature) > 350:  # Hard coded limit if user really wants to up this then up to them to edit code
            if heater.is_on():
                heater.off()
            error_text = "Pausing heater - " + MAIN_ERROR_MESSAGES["heater-too_hot"] + " " + str(shared_state.heater_temperature)
            print(error_text)
            display_manager.display_error("heater-too_hot",error_text,10,True)
        elif not heater.is_on():
            if shared_state.get_mode() != "Off":
                heater.on(power)
        if isinstance(heater, ElementHeater):
            heater.set_power(power)
    else:
        if heater.is_on():
            heater.off()  #Maybe we call this no matter what just in case?
    
    t = ','.join(map(str, [pid._last_time, shared_state.heater_temperature, thermocouple.raw_temp, pid.setpoint, power, heater.is_on(), pid.components]))
 #   print(t)

def buzzer_play_tone(buzzer, frequency, duration):
    #need to do this as a separate thread as this blocks
    buzzer.freq(frequency)
    #buzzer.duty_u16(32768) # 50% duty cycle
    buzzer.duty_u16(10000) # 
    utime.sleep_ms(duration)
    buzzer.duty_u16(0) # Stop the buzzer


class SharedState:
    def __init__(self):
    
        # All of the below hard coded can be loaded from a file or similar 
        # Need to add other stuff like butto click time, max temp, etc

        self.heater_max_duty_cycle_percent = 40
        self.input_volts = 12 # to be updated from voltage divider curcuit on adc pin - need to check on lipos nder load that they dont go below about 3.2v (* number of batteries as we arent testing each one and need to assume all battereis are of same age/quality/internal resitance)

        self.heater_resitance = 0.66  #this should not change unless coils is replaced user needs to provide this value

        #self.max_watts = (self.input_volts * self.input_volts) / self.heater_resitance # needs to be initial volts?
        self.max_watts = 120
        
        self.session_timeout = 5 * 60 * 1000   # length of time for a session before auto off (5 mins)
        self.temperature_units = 'C'       # Not tested F at all 
        
        self.setpoint = 170     # Initial PID setpoint 
        
        # When in session mode and we first hist setpoint make led change colour ?  and / or sound a buzzer 
        # When session mode about to end (5 secs?) sound buzzer so user can extens easily -
        # maybe popup with "extend session?" screen and on any click/rotate extent it

        # need to check on max temp and how long its been above 250?  re Ptfe insulation and not keeping it too ig for too long 
        # maybe have a timer for this?

        #self.power_threshold = 5  #between pid.output_limits range (1-10)
        self.power_threshold = 0 #for slower sensors like DS18X20 probally lower is better 

        # for the filtered tempterature when induction is on 
        # possibly needs adjusting for different coil sizes/current/voltages - 
        # maybe need way to reset this in the termocouple class if loading setting between reboots?
        # calibrate by placeing thermopile in induction coil and seeing effects on readings when on / off 
        # dont set this too low 
        self.heater_on_temperature_difference_threshold = 20 

        self.display_contrast = 255   # allow change by option in menu
        self.display_rotate = True
        
        # Below is stuff perhaps better to leave alone
        self.click_check_timeout = 800 # ms timeout to multi click in 
        self.max_allowed_setpoint = 299 # max allowed temperature
        

        # below are controlled by internal processes dont mess with 
        #self.temperature_readings =  {i: 20 for i in range(128)}
        self.temperature_readings =  {}
        self.heater_temperature = 0  # Overal induction heater temperature from thermocouple at the moment only deals with one 
                                     # possibly extend to deal with multpile but not to start with
                                     
        self.watt_readings = {}
        self.watts = 0
        
        self.pi_temperature = 0         # PI Pico chip temperature
        self.pi_temperature_limit = 60  # Maybe place pico board above/next to mosfet module so we get some idea hot its getting 

        #Maybe make below options have more info eg:
        # setup_rotary_values in inputhandler 
        # options screen timeout to return to home (or none for graphs etc)
        self.menu_options = ["MENU",
                             "Home Screen",
                             "Graph Setpoint",
                             "Graph Line",
                             "Graph Bar",
                             "Temp Watts Line",
                             "Watts Line",
                             "PI Temperature",
                             "Display Contrast"
                            ]
                            # Battery/power info screen  - can we get volts & amps? and move to where pid is on home? + level 
                            # Heater / coil info screen - coil length? coil ohm? (user may need to provide ohm reading at 25C)  
                            # Get resitance ? - its possible for the pico to work out the element reistance and approximate wattsage 
                            # to help user work out a limit - would need to be super careful to only happen when element has no power 
                            # Maybe use pwm heater pins and reconfigure them for to get the resitnace and need a reboot once done?
                            # Get varuous settings for elements in config file as may need to limit highest temp coil can get not to burn insulation PTFE 
                            # ie despite pid/thermocouple - so tcr? or just limit wattage on known values for wire type/length/ohms so it doesnt get too hot 
 
        self.in_menu = False  # need to add get/set fnctions? 
        self.current_menu_position = 0 # need to add get/set functions? - dont let get more than one or count of options -1
        self.menu_selection_pending = False 
        self.menu_timeout = 3 * 1000   # 3 secs
        
        self.rotary_direction = None
        self.rotary_last_mode = None

        self.session_start_time = 0
        self.session_setpoint_reached = False
        self.session_reset_pid_when_near_setpoint = True # Seems to help improve overshoot reduction by resetting pid stats once near setpoint from cold
        self._mode = "Off" 


    def get_mode(self):
        if self._mode == "Session" and (self.session_timeout - self.get_session_mode_duration()) < 0:
            session_start_time = 0
            led_pin.off()
            self._mode = "Off"  # Set off here rather than after playing sounds as this can get called again while sounds being played
            self.session_setpoint_reached = False
            buzzer_play_tone(buzzer, 1500, 200)
            utime.sleep_ms(200)
            buzzer_play_tone(buzzer, 1000, 200)
            utime.sleep_ms(200)
            buzzer_play_tone(buzzer, 500, 200)
        return self._mode

    def set_mode(self, new_mode):
        self.session_setpoint_reached = False
        if new_mode in ["Off", "Manual"]:
            if self._mode == "Session": self.session_start_time = 0
            self._mode = new_mode
        elif new_mode == "Session":
            self.session_start_time = utime.ticks_ms()
            self._mode = "Session"
        else:
            raise ValueError("Invalid mode. Must be 'Off', 'Session' or 'Manual'")
        if new_mode == "Off":
            led_pin.off()
        else:
            led_pin.on()
            pid.reset()
            print("PID Stats reset")
            
    def get_session_mode_duration(self):
        return utime.ticks_diff(utime.ticks_ms(), self.session_start_time)





###############################################################
#
# Initialisation 
#
# The led on the pico should blink brielfly before the display powers up 
# if no led blink we have a problem but do not think there is a 
# way to know so user needs to be aware that it should blink once breifly
# 
# If there is an issue with the display setup then the led will flash 3 times 
# and switch off for about a second and repeat the flashing and off.
#
# Other errors should be reported on the screen as it should now be avaliable
#
###############################################################

print("LED Initialising ...")
try:
    #led_pin = Pin(hardware_pin_led, Pin.OUT) #This is the built in pin on the pico
    led_pin = Pin("LED", Pin.OUT) 
    led_pin.on()
    utime.sleep_ms(75)
    led_pin.off()
    utime.sleep_ms(75)
    led_pin.on()
    utime.sleep_ms(75)
    led_pin.off()
    print("LED initialised.")
except Exception as e:
    print("Error initializing LED pin, unable to continue:", e)
    sys.exit()

#Maybe still add an external led - colour one perhaps to indicate above/below/on temp to see from a distance?
#make special colour for manual vs session?
#also add buzzer to sound when session about to end as you dont notice 
#maybe different buzz when first reaches setopoint that session 


print("Display Initialising ...")
display = initialize_display(hardware_pin_display_scl, hardware_pin_display_sda, led_pin)  # Move to HARDWARE.conf ?
print("Display initialised.")

shared_state = SharedState()

#config = load_config(display)  # need to get config before displaymanager setup perhaps? so if error still need to show user
#shared_state = SharedState(config)
 

# DisplayManager
try:
    display_manager = DisplayManager(display, shared_state)
    display_manager.show_startup_screen()
except Exception as e:
    error_text = "Start up failed - [display-setup] " + MAIN_ERROR_MESSAGES["display-setup"] + " " + str(e)
    print(error_text + " " + str(e))
    display.fill(0)
    display.text(error_text, 0, 0)
    display.text(str(e), 0, 15)
    display.show()
    while True:
        # Flash a LED as a backup - maybe some kind of code like one flash,flash,off,off,flash.off,off etc 
        utime.sleep_ms(100)
    sys.exit()



# Buzzer - 2 short buzzes for notifying user session has ended 
#        - 1 buzz when hitting setpoint for first time in a session
print("Buzzer Initialising ...")
buzzer = PWM(Pin(hardware_pin_buzzer))
buzzer_play_tone(buzzer, 2500, 200)  # Play a sound so we know its connected correctly
print("Buzzer initialised.")


button_pin = Pin(hardware_pin_button, Pin.IN)
print(button_pin.value())
if button_pin.value():
    enable_watchdog = True
                            
    print("Watchdog: On")
else:
    enable_watchdog = False
    utime.sleep_ms(150)
    buzzer_play_tone(buzzer, 2000, 250)
    utime.sleep_ms(150)
    buzzer_play_tone(buzzer, 1000, 250)
    display_manager.show_watchdog_off_screen()
    print("Watchdog: Off")
del button_pin

# Maybe put in function reset when options reloaded as they may affect settings
#Termocouple K type
#MAX6675

# Initialize termocouple before switching on induction heater
try:
    utime.sleep_ms(700)
    thermocouple = Thermocouple(hardware_pin_termocouple_sck, hardware_pin_termocouple_cs, hardware_pin_termocouple_so, shared_state.heater_on_temperature_difference_threshold)
    utime.sleep_ms(350)
    _, _ = thermocouple.get_filtered_temp(False)  # Sets: last_known_safe_temp - Do here rather than in class as it sometimes returns error if on class init 
except Exception as e:
    error_text = "Start up failed: " + str(e)
    print(error_text)
    while True:
        display_manager.display_error("thermocouple-setup",str(error_text))
        utime.sleep_ms(100)
    sys.exit() #?

# 1-Wire temperature sensor 
# can chain more than one together easly so will be useful for >1 coils
# not needed for the time being - maybe tie each sensor to the heater coil in ih class?
# planned to be used for monitoring temperature of zvs induction curcuit
#
#ds = ds18x20.DS18X20(onewire.OneWire(machine.Pin(17)))
#roms = ds.scan()
#print('1-wire found devices:', roms)
#ds.convert_temp()
#ds_temperature = ds.read_temp(roms[0])

# PI Temperature Sensor 
pi_temperature_sensor = machine.ADC(4)
shared_state.pi_temperature = get_pi_temperature_or_handle_error(pi_temperature_sensor)


# InputHandler
input_handler = InputHandler(rotary_clk_pin=hardware_pin_rotary_clk, rotary_dt_pin=hardware_pin_rotary_dt, button_pin=hardware_pin_button, shared_state=shared_state)

# MenuSystem
menu_system = MenuSystem(display_manager, shared_state)


# PID
#pid = PID( (shared_state.setpoint * 0.1), (shared_state.setpoint * 0.02), (shared_state.setpoint * 0.01), setpoint = shared_state.setpoint, auto_mode = False )
# when setpoint = 100  common values (10%, 2%, 1%) 
# possibly move to shared_state something like: initial_P, initial_I, initial_D?

# Ziegler-Nichols method for a system with a fast response time
#pid = PID(0.6, 1.2, 0.001, setpoint = shared_state.setpoint)

# Auto PID starting values - seems to work well with element heater
pid = PID(setpoint = shared_state.setpoint)
#pid = PID(0.3, 0.9, 0.005, setpoint = shared_state.setpoint)
# not sure if any value moving to shared state?
pid.output_limits = (0, 10)



#pid_tunings = 0.48, 0.006, 0.0001
#0.005,0
#0.00015


#pid_tunings = (shared_state.setpoint * 0.005), (shared_state.setpoint * 0.0005), (shared_state.setpoint * 0.0001)
#pid_tunings = (shared_state.setpoint * 0.006)/2, shared_state.setpoint * 0.00015,  shared_state.setpoint * 0.00005, 

print(pid.tunings)
pid.tunings = pid_tunings
print(pid.tunings)


#read before trying to tune: http://brettbeauregard.com/blog/2017/06/introducing-proportional-on-measurement/
#pid.differential_on_measurement = True   #Either this or the below not both - this is the default for PID
#pid.proportional_on_measurement = True   #Seems to be a bit odd





# InductionHeater

#ihTimer = Timer(-1) # need to replace with CustomTimer 
#heater = HeaterFactory.create_heater('induction', coil_pins=(12, 13), timer=ihTimer)

heater = HeaterFactory.create_heater('element', hardware_pin_heater, shared_state.heater_max_duty_cycle_percent)   # changing the limit will mess with PID tuning

#heater = HeaterFactory.create_heater('element', hardware_pin_heater) # no limit
#heater = HeaterFactory.create_heater('element', hardware_pin_heater, 100) # no limit


heater.off()


pidTimer = CustomTimer(371, machine.Timer.PERIODIC, timerUpdatePIDandHeater)  # need to have timer setup before calling below 
shared_state.heater_temperature, _ = get_thermocouple_temperature_or_handle_error(thermocouple, heater)

print("Timers Initialising ...")
pidTimer.start()
pid.reset()


piTempTimer = CustomTimer(903, machine.Timer.PERIODIC, timerSetPiTemp)
piTempTimer.start()



print("Timers initialised.")


# start up stuff done
############### 



# Lets enable and see if it helps when heater on and we crash
# So far from simulated tests this seems to work and heater pin is reset

if enable_watchdog: 
    watchdog = machine.WDT(timeout=(1000 * 3)) 
    print("Watchdog enabled")



start_time = utime.ticks_ms()
iteration_count = 0
refresh_rate = 0

# Sort of a load average 
#start_times = [utime.ticks_ms(), utime.ticks_ms(), utime.ticks_ms()] 
#iteration_counts = [0, 0, 0] 
#period_durations = [1000, 10000, 30000]


while True:
    if not shared_state.in_menu:
        #print(shared_state.current_menu_position)
        if shared_state.current_menu_position <= 1:
            if shared_state.rotary_last_mode != "setpoint": 
                input_handler.setup_rotary_values()
            shared_state.current_menu_position = 1
            display_manager.show_screen_home_screen(pid.components, heater)
            display_manager.display_heartbeat()
        else:
            if shared_state.rotary_last_mode != shared_state.menu_options[shared_state.current_menu_position]: 
                input_handler.setup_rotary_values()
            #print ("Displaying " + shared_state.menu_options[shared_state.current_menu_position])
            menu_system.display_selected_option()
            
    else:
        if shared_state.rotary_last_mode != "menu": 
            input_handler.setup_rotary_values()
        if shared_state.menu_selection_pending:
            menu_system.handle_menu_selection()                                               
            shared_state.menu_selection_pending = False

        elif shared_state.rotary_direction is not None:
            menu_system.navigate_menu(shared_state.rotary_direction)
            shared_state.rotary_direction = None
        else:
            pass

    
    if shared_state.get_mode() == "Session" and shared_state.session_setpoint_reached == False:
         if shared_state.heater_temperature >= (shared_state.setpoint-8):  
            shared_state.session_setpoint_reached = True
            buzzer_play_tone(buzzer, 1500, 350)
            if shared_state.session_reset_pid_when_near_setpoint:
                pid.reset()

    if enable_watchdog: watchdog.feed()

    # need to check if heater is on and temps not rising to warn user after 10 sec? 
    # eg heater pwm cable could be loose , no power to heater,  thermocouple issue 
    
    iteration_count += 1
    current_time = utime.ticks_ms()
    elapsed_time = utime.ticks_diff(current_time, start_time)
    if elapsed_time >= 1000: 
        refresh_rate = iteration_count / (elapsed_time / 1000.0)
       # print("Refresh rate:", refresh_rate, "Hz")
        iteration_count = 0
        start_time = utime.ticks_ms()

## Sort of a load average 
#    for i in range(len(iteration_counts)):
#        current_time = utime.ticks_ms()
#        elapsed_time = utime.ticks_diff(current_time, start_times[i])
#        if elapsed_time > 0 and elapsed_time >= period_durations[i]:
#            refresh_rate = iteration_counts[i] / (elapsed_time / 1000.0)
#            t = f"1s: {iteration_counts[0] / (utime.ticks_diff(utime.ticks_ms(), start_times[0]) / 1000.0):.2f} Hz, "
#            t = t + f"5s: {iteration_counts[1] / (utime.ticks_diff(utime.ticks_ms(), start_times[1]) / 1000.0):.2f} Hz, "
#            t = t + f"15s: {iteration_counts[2] / (utime.ticks_diff(utime.ticks_ms(), start_times[2]) / 1000.0):.2f} Hz"
#            print(t)
#            #print(f"{period_durations[i] / 1000}s average refresh rate: {refresh_rate} Hz")
#            iteration_counts[i] = 0
#            start_times[i] = utime.ticks_ms()
#        else:
#            iteration_counts[i] += 1
#    #print(str(iteration_counts))


    utime.sleep_ms(70)


