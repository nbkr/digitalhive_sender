#! /usr/bin/env python
# coding: utf-8

HOST = 'localhost'
PORT = 4223
INTERVAL = 15 * 60
HEIGHT = 223

# Sinkt die Spannung unter diesen Wert fährt sich der Raspberry Pi herunter.
# Man ließt unterschiedliches, von 10.5 bei Traktionsbatterien zu 11.90 bei
# Bleibatterien generell. 12.00 ist ein Kompromiss.
SHUTDOWN_VOLT = 12.00

DEST_URL = 'https://www.example.com/digitalhive/scale1/'
DEST_USER = 'scale1'
DEST_PASS = 'not_the_real_one'

###############################################################################################################################################3

from tinkerforge.ip_connection import IPConnection
from tinkerforge.bricklet_load_cell_v2 import BrickletLoadCellV2
from tinkerforge.bricklet_barometer_v2 import BrickletBarometerV2
from tinkerforge.bricklet_voltage_current_v2 import BrickletVoltageCurrentV2
from tinkerforge.bricklet_humidity_v2 import BrickletHumidityV2
from tinkerforge.bricklet_ptc_v2 import BrickletPTCV2
from functools import partial
import os
import csv
import requests
import collections
import subprocess
import time
from datetime import datetime

class HiveDataCollector:
    def __init__(self):
        self.loadcells = []
        self.loadcells.append(None)
        self.loadcells.append(None)
        self.loadcells.append(None)
        self.loadcells.append(None)

        self.hygrometer = None
        self.barometer = None
        self.voltage = None
        self.ptc = None

        self.firstlowbat = True

        # Create connection and connect to brickd
        self.ipcon = IPConnection()

        # Register Enumerate Callback
        self.ipcon.register_callback(IPConnection.CALLBACK_ENUMERATE, self.cb_enumerate)
        self.ipcon.connect(HOST, PORT)

        # Trigger Enumerate
        self.ipcon.enumerate()
        time.sleep(5) # So that the enumeration has a chance to be done.

        print "{} - Starting.".format(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

        try: 
            while True:
                self.send()
                time.sleep(INTERVAL)
        except Exception, e:
            self.ipcon.disconnect()
            print "{} - Stopping.".format(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            #print (str(e))


    def cb_enumerate(self, uid, connected_uid, position, hardware_version, firmware_version,
                     device_identifier, enumeration_type):
        #print("UID:               " + uid)
        #print("Enumeration Type:  " + str(enumeration_type))

        if enumeration_type == IPConnection.ENUMERATION_TYPE_DISCONNECTED:
            #print("")
            return

        #print("Connected UID:     " + connected_uid)
        #print("Position:          " + position)
        #print("Hardware Version:  " + str(hardware_version))
        #print("Firmware Version:  " + str(firmware_version))
        #print("Device Identifier: " + str(device_identifier))
        #print("")

        if device_identifier == BrickletLoadCellV2.DEVICE_IDENTIFIER:
            for i in range(0, len(self.loadcells)):
                if self.loadcells[i] == None:
                    key = i
                    break

            self.loadcells[key] = BrickletLoadCellV2(uid, self.ipcon)
            #self.loadcells[key].register_callback(self.loadcells[key].CALLBACK_WEIGHT, partial(self.cb_weight, key)) 
            #self.loadcells[key].set_weight_callback_configuration(1000, False, "x", 0, 0)

        if device_identifier == BrickletBarometerV2.DEVICE_IDENTIFIER:
            self.barometer = BrickletBarometerV2(uid, self.ipcon)

        if device_identifier == BrickletBarometerV2.DEVICE_IDENTIFIER:
            self.barometer = BrickletBarometerV2(uid, self.ipcon)

        if device_identifier == BrickletHumidityV2.DEVICE_IDENTIFIER:
            self.hygrometer = BrickletHumidityV2(uid, self.ipcon)

        if device_identifier == BrickletVoltageCurrentV2.DEVICE_IDENTIFIER:
            self.voltage = BrickletVoltageCurrentV2(uid, self.ipcon)

        if device_identifier == BrickletPTCV2.DEVICE_IDENTIFIER:
            #self.ptc = BrickletPTCV2(uid, self.ipcon)
            # No Temp sensor yet.
            pass

    def send(self):
        # The order is important, its the order it get's written into the file.
        data = collections.OrderedDict()
        data['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M')
        data['weight'] = 0.0 
        data['temp'] = '?'
        data['pressure-qfe'] = '?'
        data['pressure-qff'] = '?'
        data['humidity'] = '?'
        data['innertemp'] = '?'
        data['height'] = '?'
        data['bat'] = '?'

        # Weight Data
        for i in self.loadcells:
            if i is not None:
                data['weight'] = data['weight'] + i.get_weight()
            else:
                data['weight'] = '?'
                break

        if data['weight'] != '?':
            data['weight'] = round(data['weight'] / 1000.00, 1)

        # We need the tempreture first
        temp = '?'
        if self.hygrometer is not None:
            data['humidity'] = int(round(float(self.hygrometer.get_humidity() / 100.00), 0))
            temp = self.hygrometer.get_temperature() / 100.00 # Need the detailed value for qff value.
            data['temp'] = int(round((temp), 0))

        # Other values
        if self.barometer is not None:
            qfe = self.barometer.get_air_pressure() / 1000.00
            data['pressure-qfe'] = int(round(qfe, 0))
            # In order to calculate the value as the weather forecast does, we need the current
            # temprature and the height of the location.
            #
            # Interesstingly enough, I don't think, one could set the later
            # value in the barometers you can buy. That's why they don't give a
            # read out, just a 'good weather, bad weather'. Theid either would
            # have to give the QFE value, which would differ quite a bit from the
            # weather forecast and therefore be consideres 'wrong' by the customers
            # or the customer would have to enter their location value.
            #
            # I planed on doing the calculation here. But that would basically fix
            # the value. If you move the sensor to a different location and forgot
            # to change the value, it would modify the data.csv. I'd rather not
            # do that. On the other hand: If you do move the scale, and change the
            # value afterwards, it would change all the old values. So we would
            # actually have to introduce a 'until' value for the height. Also,
            # we need the temperture to the the forecast value. This makes it
            # difficult to calculate in the Javascript / backend software.
            #
            # So, two values it is. The readout from the sensor, and a calculated
            # one. This way we have a simple value (qff) and the exact value if
            # we ever decide to recalculate it.
            # 
            # We also send the HEIGHT value, so that we can use it for later
            # recalculation

            if HEIGHT is not None and temp is not '?':
                qfe = float(data['pressure-qfe'])
                Tg = 0.0065
                H = HEIGHT
                Tfe = temp

                qff = qfe / (1 - Tg * H / (273.15 + Tfe + Tg * H)) ** (0.034163 / Tg)
                data['pressure-qff'] = int(round(qff, 0))



        if self.ptc is not None:
            data['innertemp'] = round((self.ptc.get_temperature() / 100.00), 1)

        if HEIGHT is not None:
            data['height'] = HEIGHT

        if self.voltage is not None:
            data['bat'] = round(float(self.voltage.get_voltage() / 1000.00), 2)


        if not os.path.exists('data.csv'):
            with open('data.csv', 'w') as csvfile:
                csvfile.write("timestamp,weight,temp,pressure-qfe,pressure-qff,humidity,innertemp,height,bat\n")

        with open('data.csv', 'a') as csvfile:
            datawriter = csv.DictWriter(csvfile, data.keys(), delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL, lineterminator='\n')
            datawriter.writerow(data)

        try:
            r = requests.post(DEST_URL, data=data, auth=(DEST_USER, DEST_PASS), timeout=10)
        except:
            pass

        # Emergency Shutdown if voltage is to low
        if data['bat'] is not '?' and data['bat'] < SHUTDOWN_VOLT:
            if self.firstlowbat == True:
                # We want to shutdown only if it happend the second time. Why?
                # Otherwise, if the battery runs out, and we attach the Raspberry pi to
                # a normal power supply - without the battery on - the battery read out
                # will show '0.0' as there is no current running. So it would more or
                # less immediately shutdown the raspberry pi again. Without a chance
                # to disable the battery monitor.
                self.firstlowbat = False
            else:
                subprocess.call('sudo shutdown -h now', shell=True);



HiveDataCollector()
