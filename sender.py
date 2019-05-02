#! /usr/bin/env python3
# coding: utf-8

from tinkerforge.ip_connection import IPConnection
from tinkerforge.bricklet_load_cell_v2 import BrickletLoadCellV2
from tinkerforge.bricklet_barometer_v2 import BrickletBarometerV2
from tinkerforge.bricklet_voltage_current_v2 import BrickletVoltageCurrentV2
from tinkerforge.bricklet_humidity_v2 import BrickletHumidityV2
from tinkerforge.bricklet_ptc_v2 import BrickletPTCV2
from functools import partial
import os
import yaml
import csv
import requests
import collections
import subprocess
import time
from datetime import datetime

class HiveDataCollector:
    def __init__(self):
        self.loadcell = None
        self.hygrometer = None
        self.barometer = None
        self.voltage = None
        self.ptc = None

        self.firstlowbat = True

        self.config = {}

        # Reading config
        with open(os.path.expanduser("~/digitalhive_sender.conf"), 'r') as stream:
            try:
                self.config = yaml.safe_load(stream)
            except yaml.YAMLError as exc:
                print("Konnte Konfiguration nicht laden.")
                sys.exit(1)

        # Create connection and connect to brickd
        self.ipcon = IPConnection()

        # Register Enumerate Callback
        self.ipcon.register_callback(IPConnection.CALLBACK_ENUMERATE, self.cb_enumerate)
        self.ipcon.connect('localhost', 4223)

        # Trigger Enumerate
        self.ipcon.enumerate()
        time.sleep(5) # So that the enumeration has a chance to be done.

        print("{} - Starting.".format(datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

        try: 
            while True:
                self.send()
                time.sleep(int(self.config['interval']) * 60)
        except Exception as e:
            self.ipcon.disconnect()
            print("{} - Stopping.".format(datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
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
            self.loadcell = BrickletLoadCellV2(uid, self.ipcon)
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
            self.ptc = BrickletPTCV2(uid, self.ipcon)
            if not self.ptc.is_sensor_connected():
                self.ptc = None
                return
            
            self.ptc.set_wire_mode(BrickletPTCV2.WIRE_MODE_4)


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
        if self.loadcell is not None:
            data['weight'] = self.loadcell.get_weight()
            data['weight'] = round(data['weight'] / 1000.00, 2)

        # We need the tempreture first
        temp = '?'
        if self.hygrometer is not None:
            data['humidity'] = round(float(self.hygrometer.get_humidity() / 100.00), 1)
            temp = self.hygrometer.get_temperature() / 100.00 # Need the detailed value for qff value.
            data['temp'] = round((temp), 1)

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

            if self.config['height'] is not None and temp is not '?':
                qfe = float(data['pressure-qfe'])
                Tg = 0.0065
                H = self.config['height']
                Tfe = temp

                qff = qfe / (1 - Tg * H / (273.15 + Tfe + Tg * H)) ** (0.034163 / Tg)
                data['pressure-qff'] = int(round(qff, 0))



        if self.ptc is not None:
            data['innertemp'] = round((self.ptc.get_temperature() / 100.00), 1)

        if self.config['height'] is not None:
            data['height'] = self.config['height']

        if self.voltage is not None:
            data['bat'] = round(float(self.voltage.get_voltage() / 1000.00), 2)


        if not os.path.exists('data.csv'):
            with open('data.csv', 'w') as csvfile:
                csvfile.write("timestamp,weight,temp,pressure-qfe,pressure-qff,humidity,innertemp,height,bat\n")

        with open('data.csv', 'a') as csvfile:
            datawriter = csv.DictWriter(csvfile, data.keys(), delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL, lineterminator='\n')
            datawriter.writerow(data)

        try:
            r = requests.post("{}/admin/newdata.php".format(self.config['dest_url']), data=data, auth=(self.config['dest_user'], self.config['dest_passt']), timeout=10)
        except:
            pass

        # Emergency Shutdown if voltage is to low
        if data['bat'] is not '?' and data['bat'] < self.config['shutdownvolt']:
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
