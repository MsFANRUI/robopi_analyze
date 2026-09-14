#!/usr/bin/env python3
# Copyright (C) 2026 wentywenty
# 通过 USB BootROM 协议与 HPM5E00 通信，执行擦除、写入和设备控制操作。
#
# SPDX-FileCopyrightText: 
# Copyright (c) 2026 https://github.com/hpmicro]
#
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import division, print_function

import argparse
import base64
import binascii
import copy
import hashlib
import inspect
import io
import itertools
import os
import re
import shlex
import string
import struct
import sys
import time
import zlib
import crcmod
import hid
import csv
import time
import random
import usb.core
import usb.util

try:
    import serial
except ImportError:
    print("Pyserial is not installed for %s. Check the README for installation instructions." % (sys.executable))
    raise

try:
    if "serialization" in serial.__doc__ and "deserialization" in serial.__doc__:
        raise ImportError("""
hpmtool.py depends on pyserial, but there is a conflict with a currently installed package named 'serial'.

You may be able to work around this by 'pip uninstall serial; pip install pyserial' \
but this may break other installed Python software that depends on 'serial'.
""")
except TypeError:
    pass  # __doc__ returns None for pyserial

try:
    import serial.tools.list_ports as list_ports
except ImportError:
    print("The installed version (%s) of pyserial appears to be too old for hpmtool.py (Python interpreter %s). "
          "Check the README for installation instructions." % (sys.VERSION, sys.executable))
    raise
except Exception:
    if sys.platform == "darwin":
        list_ports = None
    else:
        raise

__version__ = "1.1.0"

HPM_OK = 0
HPM_ERROR = -1

MAX_UINT32 = 0xffffffff
MAX_UINT24 = 0xffffff

DEFAULT_TIMEOUT = 3                   # timeout for most flash operations
START_FLASH_TIMEOUT = 20              # timeout for starting flash (may perform erase)
CHIP_ERASE_TIMEOUT = 120              # timeout for full chip erase
MAX_TIMEOUT = CHIP_ERASE_TIMEOUT * 2  # longest any command can run
SYNC_TIMEOUT = 0.1                    # timeout for syncing with bootloader
READ_TIMEOUT = 0.1                    # timeout for syncing with bootloader
ERASE_REGION_TIMEOUT_PER_MB = 30      # timeout (per megabyte) for erasing a region
ERASE_WRITE_TIMEOUT_PER_MB = 40       # timeout (per megabyte) for erasing and writing data
DEFAULT_SERIAL_WRITE_TIMEOUT = 10     # timeout for serial port write
DEFAULT_CONNECT_ATTEMPTS = 7          # default number of times to try connection
WRITE_BLOCK_ATTEMPTS = 3              # number of times to try writing a data block

SUPPORTED_CHIPS = ['hpm6400', 'hpm6700', 'hpm6800', 'hpm6300', 'hpm6200', 'hpm5300', 'hpm6e00', 'hpm6p00','hpm5e00']

HPM_IMAGE_MAGIC = 0xfcf9

#    [31:28] Flash probe type
#      0 - SFDP SDR / 1 - SFDP DDR
#      2 - 1-4-4 Read (0xEB, 24-bit address) / 3 - 1-2-2 Read(0xBB, 24-bit address)
#      4 - HyperFLASH 1.8V / 5 - HyperFLASH 3V
#      6 - OctaBus DDR (SPI -> OPI DDR)
#      8 - Xccela DDR (SPI -> OPI DDR)
#      10 - EcoXiP DDR (SPI -> OPI DDR)
FLASH_PROBE_TYPE = {
    "SFDP_SDR": 0x0,
    "SFDP_DDR": 0x1,
    "1-4-4_Read": 0x2,
    "1-2-2_Read": 0x3,
    "HyperFLASH_1.8V": 0x4,
    "HyperFLASH_3V": 0x5,
    "OctaBus_DDR": 0x6,
    "Xccela_DDR": 0x8,
    "EcoXiP_DDR": 0xA,
}

#    [23:20] Command Pads after Configuring FLASH
#      0 - SPI / 1 - DPI / 2 - QPI / 3 - OPI
FLASH_PAD_AFTER = {
    'SPI': 0,
    'DPI': 1,
    'QPI': 2,
    'OPI': 3,
}

# [19:16] Quad Enable Sequence (for the device support SFDP 1.0 only)
#  Quad Enable Sequence
#     0 - Not needed
#      1 - QE bit is at bit 6 in Status Register 1
#      2 - QE bit is at bit1 in Status Register 2
#      3 - QE bit is at bit7 in Status Register 2
#      4 - QE bit is at bit1 in Status Register 2 and should be programmed by 0x31
FLASH_QES = {
    'not_needed': 0,
    'reg1bit6': 1,
    'reg2bit1': 2,
    'reg2bit7': 3,
    'reg2bit1_0x31': 4,
}

# [19:16] Quad Enable Sequence (for the device support SFDP 1.0 only)
#  0 - Not used
#  1 - SPI mode
#  2 - Internal loopback
#  3 - External DQS
FLASH_MISC = {
    'not_used': 0,
    'spi_mode': 1,
    'loopback': 2,
    'qds': 3,
}

# [3:0] Frequency option
# 1 - 30MHz / 2 - 50MHz / 3 - 66MHz / 4 - 80MHz / 5 - 100MHz / 6 - 120MHz / 7 - 133MHz / 8 - 166MHz
FLASH_FREQ = {
    '30m': 0x1,
    '50m': 0x2,
    '66m': 0x3,
    '80m': 0x4,
    '100m': 0x5,
    '120m': 0x6,
    '133m': 0x7,
    '166m': 0x8,
}
#    [19:16] IO voltage
#      0 - 3V / 1 - 1.8V
FLASH_VOLTAGE = {
    '3.3V': 0,
    '1.8V': 1,
}

#    [15:12] Pin group
#      0 - 1st group / 1 - 2nd group
FLASH_GROUP = {
    '1st_group': 0,
    '2nd_group': 1,
}

#    [11:8] Connection selection
#      0 - CA_CS0 / 1 - CB_CS0 / 2 - CA_CS0 + CB_CS0 (Two FLASH connected to CA and CB respectively)
FLASH_SELECTION = {
    'CA_CS0': 0,
    'CB_CS0': 1,
    'CA_CS0+CB_CS0': 2,
}
#    [7:0] Drive Strength
#      0 - Default value

#    [7:0] Flash Size Option
#      0 - 4MB / 1 - 8MB / 2 - 16MB
FLASH_SIZE = {
    "4MB":  0x0,
    "8MB":  0x1,
    "16MB": 0x2,
}

def get_item_key(item_name, value):
    for key, val in item_name.items():
        if val == value:
            return key
    return None

def timeout_per_mb(seconds_per_mb, size_bytes):
    """ Scales timeouts which are size-specific """
    result = seconds_per_mb * (size_bytes / 1e6)
    if result < DEFAULT_TIMEOUT:
        return DEFAULT_TIMEOUT
    return result


def _chip_to_rom_loader(chip):
    return {
        'hpm': HPMROM,
        'hpm6400': HPM6700ROM,
        'hpm6700': HPM6700ROM,
        'hpm6300': HPM6300ROM,
        'hpm6200': HPM6200ROM,
        'hpm6800': HPM6800ROM,
        'hpm5300': HPM5300ROM,
        'hpm6e00': HPM6e00ROM,
        'hpm6p00': HPM6p00ROM,
        'hpm5e00': HPM5e00ROM,
    }[chip]

def get_serial_connected_device(serial_list, connect_attempts, port, initial_baud, chip='auto', trace=False,
                                 before='default_reset'):
    _hpm = None
    for each_port in reversed(serial_list):
        print("Serial port %s" % each_port)
        try:
            if chip == 'auto':
                _hpm = HPMLoader.detect_chip(each_port, initial_baud, before, trace,
                                             connect_attempts)
            else:
                chip_class = _chip_to_rom_loader(chip)
                _hpm = chip_class(each_port, initial_baud, trace)
                _hpm.connect(before, connect_attempts)
            break
        except (FatalError, OSError) as err:
            if port is not None:
                raise
            print("%s failed to connect: %s" % (each_port, err))
            if _hpm and _hpm._port:
                _hpm._port.close()
            _hpm = None
    return _hpm

def get_usb_connected_device(usb_list, connect_attempts, chip='auto', trace=False):
    _hpm = None
    try:
        if chip == 'auto':
            print("Auto-detecting chip type...")
            # 假设自动检测逻辑可以通过 USB 设备实现
        else:
            # 使用指定芯片类进行连接
            chip_class = _chip_to_rom_loader(chip)
            vid = chip_class.USB_VID  # 访问类属性 VID
            pid = chip_class.USB_PID  # 访问类属性 PID
            target_device = None
            for device in usb_list:
                if device['vendor_id'] == vid and device['product_id'] == pid:  # 替换为实际的 Vendor ID 和 Product ID
                    target_device = device
                    break
            if target_device is None:
                raise FatalError("No USB HID device found")

            print(f"Found USB HID device: Vendor ID={hex(target_device['vendor_id'])}, Product ID={hex(target_device['product_id'])}")
            # 打开设备
            dev = hid.device()
            dev.open(target_device['vendor_id'], target_device['product_id'])

            # 打印设备信息
            print(f"Manufacturer: {dev.get_manufacturer_string()}")
            print(f"Product: {dev.get_product_string()}")
            _hpm = chip_class(dev, 0, trace_enabled=trace)

    except (FatalError, OSError, usb.core.USBError) as err:
        print("Error: %s" % err)
        _hpm = None
    return _hpm

def check_supported_function(func, check_func):
    def inner(*args, **kwargs):
        obj = args[0]
        if check_func(obj):
            return func(*args, **kwargs)
        else:
            raise NotImplementedInROMError(obj, func)
    return inner

def stub_function_only(func):
    """ Attribute for a function only supported in the software stub loader """
    return check_supported_function(func, lambda o: o.IS_STUB)

def stub_and_hpm_function_only(func):
    return check_supported_function(func, lambda o: o.IS_STUB or isinstance(o, HPMROM))


PYTHON2 = sys.version_info[0] < 3  # True if on pre-Python 3

# Function to return nth byte of a bitstring
# Different behaviour on Python 2 vs 3
if PYTHON2:
    def byte(bitstr, index):
        return ord(bitstr[index])
else:
    def byte(bitstr, index):
        return bitstr[index]

# Provide a 'basestring' class on Python 3
try:
    basestring
except NameError:
    basestring = str

def print_overwrite(message, last_line=False):
    """ Print a message, overwriting the currently printed line.

    If last_line is False, don't append a newline at the end (expecting another subsequent call will overwrite this one.)

    After a sequence of calls with last_line=False, call once with last_line=True.

    If output is not a TTY (for example redirected a pipe), no overwriting happens and this function is the same as print().
    """
    if sys.stdout.isatty():
        print("\r%s" % message, end='\n' if last_line else '')
    else:
        print(message)

def format_bytes(data):
    # 将字节串转换为 16 进制字符串，并在每个字节之间添加空格
    hex_str = ' '.join(f'{b:02x}' for b in data)
    # 每 32 个字节输出一个换行
    formatted_str = '\n'.join([hex_str[i:i+96] for i in range(0, len(hex_str), 96)])
    return formatted_str

def format_word_hex(data):
    # 确保数据长度是 4 的倍数
    if len(data) % 4 != 0:
        raise ValueError("Data length must be a multiple of 4")
    
    # 将字节数据解包为 32 位整数
    num_32bit_ints = len(data) // 4
    unpacked_data = struct.unpack(f'<{num_32bit_ints}I', data)
    
    # 将每个 32 位整数格式化为 16 进制字符串
    hex_str = ' '.join(f'0x{val:08x}' for val in unpacked_data)
    # 每 8 个 32 位整数输出一个换行
    formatted_str = '\n'.join([' '.join(hex_str.split()[i:i+8]) for i in range(0, len(hex_str.split()), 8)])
    return formatted_str

class HPMLoader(object):
    """ Base class for HPMLoader bootloader interaction"""

    CHIP_NAME = "HPMicro device"
    IS_STUB = False

    DEFAULT_PORT = "/dev/ttyUSB0"
    # Default baudrate. The ROM auto-bauds, so we can use more or less whatever we want.
    DEFAULT_BAUD = 115200

    UART_DATA_MAX = 508
    USB_DATA_MAX = 508
    USB_PACKET_SIZE = 516  # Fixed packet size for USB HID communication

    HPM_START_HEAD = 0x5A
    HPM_DATA_TYPE = 0xA5
    HPM_USB_SEND_HEAD = 0x01
    HPM_USB_RECV_HEAD = 0x02

    HPM_RECV_ACK = 0xA1
    HPM_RECV_NAK = 0xA2
    HPM_RECV_ABORT = 0xA3

    # Commands sent to ROM
    HPM_QUERY_RTE = 0x01
    HPM_CONFIG_RTE = 0x02
    HPM_CONFIG_MEMORY = 0x03
    HPM_WRITE_MEMORY = 0x04
    HPM_READ_MEMORY = 0x05
    HPM_LOAD_IMAGE = 0x06
    HPM_ERASE = 0x07
    HPM_RESET = 0x08
    HPM_GEN_FW_BLOB = 0x09

    # Flash sector size, minimum unit of erase.
    FLASH_SECTOR_SIZE = 0x1000

    sync_stub_detected = False

    def __init__(self, port=DEFAULT_PORT, baud=DEFAULT_BAUD, trace_enabled=False):
        """Base constructor for HPMLoader bootloader interaction"""

        print("Initializing HPMLoader...")
        self.secure_download_mode = False  # flag is set to True if hpmtool detects the ROM is in Secure Download Mode
        self.stub_is_disabled = False  # flag is set to True if hpmtool detects conditions which require the stub to be disabled

        self._trace_enabled = trace_enabled

        # 检查设备类型
        if isinstance(port, str):  # 串口设备
            import serial
            self._port = serial.serial_for_url(port)
            self._set_port_baudrate(baud)
            self._slip_reader = slip_reader(self._port, self._trace_enabled)
            # 设置写超时，防止写操作阻塞
            self._is_hid = False
            try:
                self._port.write_timeout = DEFAULT_SERIAL_WRITE_TIMEOUT
            except NotImplementedError:
                self._port.write_timeout = None
        elif isinstance(port, hid.device):  # USB HID 设备
            self._usb = port
            self._is_hid = True
            print("Initialized with USB HID device.")
        else:
            raise ValueError("Unsupported port type. Must be a string (serial) or HID device.")

    @property
    def serial_port(self):
        return self._port.port

    def _set_port_baudrate(self, baud):
        try:
            self._port.baudrate = baud
        except IOError:
            raise FatalError("Failed to set baud rate %d. The driver may not support this rate." % baud)

    @staticmethod
    def detect_chip(port=DEFAULT_PORT, baud=DEFAULT_BAUD, connect_mode='default_reset', trace_enabled=False,
                    connect_attempts=DEFAULT_CONNECT_ATTEMPTS):
        """ Use serial access to detect the chip type.

        This routine automatically performs HPMLoader.connect() (passing
        connect_mode parameter) as part of querying the chip.
        """
        inst = None
        detect_port = HPMLoader(port, baud, trace_enabled=trace_enabled)
        detect_port.connect(connect_mode, connect_attempts, detecting=True)
        
        try:
            print('Detecting chip type...', end='')
            pass
        except (UnsupportedCommandError, struct.error, FatalError) as e:
            pass
        finally:
            if inst is not None:
                print(' %s' % inst.CHIP_NAME, end='')
                if detect_port.sync_stub_detected:
                    inst = inst.STUB_CLASS(inst)
                    inst.sync_stub_detected = True
                print('')  # end line
                return inst
        raise FatalError("Unexpected CHIP magic value 0x%08x. Failed to autodetect chip type." % (chip_magic_value))

    """ Read a SLIP packet from the serial port """
    def next(self):
        return next(self._slip_reader)
    
    def read(self, size):
        if self._is_hid:
            p = bytes(self._usb.read(self.USB_PACKET_SIZE))
            return p[:size]
        else :
            return self._port.read(size)

    """ Write bytes to the serial port while performing SLIP escaping """
    def write(self, packet):
        if self._is_hid:
            # Pad the packet to the fixed size
            if len(packet) < self.USB_PACKET_SIZE:
                packet += b'\x00' * (self.USB_PACKET_SIZE - len(packet))
            self._usb.write(packet)
        else :
            self._port.write(packet)

    def close(self):
        if self._is_hid:
            self._usb.close()
        else :
            self._port.close()

    def trace(self, message, *format_args):
        if self._trace_enabled:
            now = time.time()
            try:
                delta = now - self._last_trace
            except AttributeError:
                delta = 0.0
            self._last_trace = now
            prefix = "TRACE +%.3f " % delta
            print(prefix + (message % format_args))

    """ Send a request and read the response """
    def command_serial(self, cmd, arg_num, cmd_type, data=b"", read_len = 8, timeout=DEFAULT_TIMEOUT):
        saved_timeout = self._port.timeout
        new_timeout = min(timeout, MAX_TIMEOUT)
        if new_timeout != saved_timeout:
            self._port.timeout = new_timeout

        try:
            self.trace("command cmd=0x%02x,arg_num=0x%02x, cmd_type=0x%02x data len=%s timeout=%.3f data=%s",
                           cmd, arg_num, cmd_type, len(data), timeout, HexFormatter(data))
            
            data_total_num = len(data)
            data_num = data_total_num if data_total_num < self.UART_DATA_MAX else self.UART_DATA_MAX
            pkt = struct.pack(b'<BBHBBBB', self.HPM_START_HEAD, self.HPM_DATA_TYPE, data_num + 4, cmd, arg_num, cmd_type, 0x00) + data[:data_num]
            crc16_xmodem = crcmod.predefined.mkCrcFun('xmodem')
            crc_result = crc16_xmodem(pkt)
            pkt += struct.pack(b'<H', crc_result)
            self.write(pkt)
            repe_data_len = data_total_num - data_num
            while repe_data_len:
                print(".", end='', flush=True)
                p = self.read(2)
                if len(p) < 2: # 1 byte head + 1 byte ack
                    continue
                (ss_head, ss_type) = struct.unpack('<BB', p[:2])
                if ss_head != self.HPM_START_HEAD or ss_type != self.HPM_RECV_ACK:
                    return HPM_ERROR, b''
                data_num = repe_data_len if repe_data_len < self.UART_DATA_MAX else self.UART_DATA_MAX
                data_start = data_total_num - repe_data_len
                data_end   = data_start + data_num
                pkt = struct.pack(b'<BBHBBBB', self.HPM_START_HEAD, self.HPM_DATA_TYPE, data_num + 4, cmd, 0x0, 0x1, 0x00) + data[data_start:data_end]
                crc_result = crc16_xmodem(pkt)
                pkt += struct.pack(b'<H', crc_result)
                self.write(pkt)
                repe_data_len -= data_num
          
            for retry in range(100):
                p = self.next()
                if len(p) < 12: # 1 byte head + 1 byte ack + 1 byte head + 1 byte type + 2 byte len + 1 byte op + 1 byte cmd num + 1 byte cmd type + 1byte reserve  + 2 byte crc16
                    continue
                (ss_head, ss_type, rs_head, rs_type, rs_len, rs_cmd) = struct.unpack('<BBBBHB', p[:7])
                if ss_head != self.HPM_START_HEAD or ss_type != self.HPM_RECV_ACK:
                    return HPM_ERROR, b''
                
                rs_data = p[6:-2] # keep op code and delete crc16.

                if  rs_head != self.HPM_START_HEAD or rs_type != self.HPM_DATA_TYPE or \
                    len(rs_data) != rs_len or rs_cmd == cmd:
                    ack = struct.pack(b'<BB', self.HPM_START_HEAD, self.HPM_RECV_ACK)
                    self.write(ack)
                    return HPM_OK, rs_data
                else:
                    nack = struct.pack(b'<BB', self.HPM_START_HEAD, self.HPM_RECV_ABORT)
                    self.write(nack)
                    return HPM_ERROR, b''
        finally:
            if new_timeout != saved_timeout:
                self._port.timeout = saved_timeout
        raise FatalError("Response doesn't match request")

    def command_usb(self, cmd, arg_num, cmd_type, data=b"", read_len = 8, timeout=DEFAULT_TIMEOUT):
        """
        Send a command to the device via USB HID with fixed-length packets (516 bytes).
        CRC logic has been removed.
        """
        try:
            self.trace("command cmd=0x%02x,arg_num=0x%02x, cmd_type=0x%02x data len=%s timeout=%.3f data=%s",
                    cmd, arg_num, cmd_type, len(data), timeout, HexFormatter(data))

            # Prepare the initial packet
            data_total_num = len(data)
            data_num = min(data_total_num, self.USB_DATA_MAX)
            pkt = struct.pack(b'<BBHBBBB', self.HPM_USB_SEND_HEAD, self.HPM_DATA_TYPE, data_num + 4, cmd, arg_num, cmd_type, 0x00) + data[:data_num]
            self.write(pkt)

            repe_data_len = data_total_num - data_num
            while repe_data_len:
                p = self.read(8)
                (ss_head, ss_type) = struct.unpack('<BB', p[:2])
                if ss_head != self.HPM_USB_RECV_HEAD or ss_type != self.HPM_RECV_ACK:
                    return HPM_ERROR, b''
                data_num = repe_data_len if repe_data_len < self.USB_DATA_MAX else self.USB_DATA_MAX
                data_start = data_total_num - repe_data_len
                data_end   = data_start + data_num
                pkt = struct.pack(b'<BBHBBBB', self.HPM_USB_SEND_HEAD, self.HPM_DATA_TYPE, data_num + 4, cmd, 0x0, 0x1, 0x00) + data[data_start:data_end]
                self.write(pkt)
                repe_data_len -= data_num
            # Read the response
            for retry in range(100):
                # ACK
                p = self.read(8)
                (ss_head, ss_type, rs_head, rs_type, rs_len, rs_cmd) = struct.unpack('<BBBBHB', p[:7])
                if ss_head != self.HPM_USB_RECV_HEAD or ss_type != self.HPM_RECV_ACK:
                    return HPM_ERROR, b''
                #if ss_head != self.HPM_USB_RECV_HEAD or ss_type != self.HPM_RECV_ACK:
                #    return HPM_ERROR, b''
                # DATA
                p = self.read(self.USB_PACKET_SIZE)
                (rs_head, rs_type, rs_len, rs_cmd) = struct.unpack('<BBHB', p[:5])
        
                start = 4
                end = read_len + start
                rs_data = p[start:end]

                if rs_head == self.HPM_USB_RECV_HEAD and rs_type == self.HPM_DATA_TYPE and rs_cmd == cmd:
                    # Send acknowledgment
                    ack = struct.pack(b'<BB', self.HPM_USB_SEND_HEAD, self.HPM_RECV_ACK)
                    self.write(ack)
                    return HPM_OK, rs_data
                else:
                    # Send negative acknowledgment
                    nack = struct.pack(b'<BB', self.HPM_USB_SEND_HEAD, self.HPM_RECV_ABORT)
                    self.write(nack)
                    return HPM_ERROR, b''
            
        except Exception as e:
            self.trace(f"Error during USB command: {e}")
            return HPM_ERROR, b''
        raise FatalError("Response doesn't match request")

    def command(self, cmd, arg_num, cmd_type, data=b"", read_len = 8, timeout=1.0):
        """
        Unified interface for sending a command, automatically switches between serial and USB HID.
        """
        if self._is_hid:
            return self.command_usb(cmd, arg_num, cmd_type, data, read_len = read_len, timeout=timeout)
        else:
            return self.command_serial(cmd, arg_num, cmd_type, data, read_len = read_len, timeout=timeout)
        
    def flush_input(self):
        self._port.flushInput()
        self._slip_reader = slip_reader(self._port, self.trace)

    def sync(self):
        ret, _ = self.command(self.HPM_QUERY_RTE, 0x01, 0x00, 4 * b'\x00', 8, timeout=SYNC_TIMEOUT)
        self.sync_stub_detected = ret == HPM_OK

    def _setDTR(self, state):
        self._port.setDTR(state)

    def _setRTS(self, state):
        self._port.setRTS(state)
        # Work-around for adapters on Windows using the usbser.sys driver:
        # generate a dummy change to DTR so that the set-control-line-state
        # request is sent with the updated RTS state and the same DTR state
        self._port.setDTR(self._port.dtr)

    def _connect_attempt(self, mode='default_reset', usb_jtag_serial=False, extra_delay=False):
        """ A single connection attempt """
        last_error = None
        for _ in range(5):
            try:
                self.flush_input()
                self._port.flushOutput()
                self.sync()
                return None
            except FatalError as e:
                print('.', end='')
                sys.stdout.flush()
                time.sleep(0.05)
                last_error = e
        return last_error

    def get_memory_region(self, name):
        """ Returns a tuple of (start, end) for the memory map entry with the given name, or None if it doesn't exist
        """
        try:
            return [(start, end) for (start, end, n) in self.MEMORY_MAP if n == name][0]
        except IndexError:
            return None

    def connect(self, mode='default_reset', attempts=DEFAULT_CONNECT_ATTEMPTS, detecting=False, warnings=True):
        """ Try connecting repeatedly until successful, or giving up """
        if warnings and mode in ['no_reset', 'no_reset_no_sync']:
            print('WARNING: Pre-connection option "{}" was selected.'.format(mode),
                  'Connection may fail if the chip is not in bootloader or flasher stub mode.')
        print('Connecting...', end='')
        sys.stdout.flush()
        last_error = None

        usb_jtag_serial = (mode == 'usb_reset')

        try:
            for _, extra_delay in zip(range(attempts) if attempts > 0 else itertools.count(), itertools.cycle((False, True))):
                last_error = self._connect_attempt(mode=mode, usb_jtag_serial=usb_jtag_serial, extra_delay=extra_delay)
                if last_error is None:
                    break
        finally:
            print('')  # end 'Connecting...' line

        if last_error is not None:
            raise FatalError('Failed to connect to {}: {}'
                             '\nFor troubleshooting steps visit: '
                             'https://docs.hpmciro.com/projects/hpmtool/en/latest/index.html'.format(self.CHIP_NAME, last_error))

        print('Connected to %s' % self.CHIP_NAME)

    def parse_feature_bits(self, value):
        def bit(v, i):
            return (v >> i) & 1

        print("Bit[26] 是否支持 SM4_CCM        :",    "YES" if bit(value, 26) else "NO")
        print("Bit[25] 是否支持 AES_CCM(256)   :",    "YES" if bit(value, 25) else "NO")
        print("Bit[24] 是否支持 AES_CCM(128)   :",    "YES" if bit(value, 24) else "NO")
        print("Bit[19] 是否支持 SM2            :",    "YES" if bit(value, 19) else "NO")
        print("Bit[18] 是否支持 ECDSA P521     :",    "YES" if bit(value, 18) else "NO")
        print("Bit[17] 是否支持 ECDSA P384     :",    "YES" if bit(value, 17) else "NO")
        print("Bit[16] 是否支持 ECDSA P256     :",    "YES" if bit(value, 16) else "NO")
        print("Bit[11] 是否支持设备配置块      :",    "YES" if bit(value, 11) else "NO")
        print("Bit[10] 是否支持命令容器        :",    "YES" if bit(value, 10) else "NO")
        print("Bit[9]  是否支持加密启动        :",    "YES" if bit(value, 9) else "NO")
        print("Bit[8]  是否支持安全启动        :",    "YES" if bit(value, 8) else "NO")
        print("Bit[3]  是否支持在系统编程 (ISP):",    "YES" if bit(value, 3) else "NO")
        print("Bit[2]  是否支持串行启动        :",    "YES" if bit(value, 2) else "NO")
        print("Bit[1]  是否支持恢复启动        :",    "YES" if bit(value, 1) else "NO")
        print("Bit[0]  是否支持主启动 rom_info :",    "YES" if bit(value, 0) else "NO")
    
    def query_rte(self, rte_num, read_len = 8):
        cmd_data = struct.pack(b'<I', rte_num)
        ret, rsp_data = self.command(self.HPM_QUERY_RTE, 0x01, 0x00, cmd_data, read_len, timeout=SYNC_TIMEOUT)
        if ret != HPM_OK or len(rsp_data) < 8:
            print("HPM_ERROR ret:%d, len:%d" % (ret, len(rsp_data)))
            return HPM_ERROR, rsp_data
        (cmd_flag, cmd_num, cmd_type, cmd_null, cmd_ret) = struct.unpack('<BBBBI', rsp_data[:8])
        if cmd_flag != self.HPM_QUERY_RTE or cmd_ret != 0x00:
            print("HPM_ERROR cmd_ret:%d", cmd_ret)
            return HPM_ERROR, rsp_data
        return ret, rsp_data

    def query_rte_rom(self):
        ret, rsp_data = self.query_rte(0x00, 24)
        if ret != HPM_OK:
            return HPM_ERROR
        (rom_ver, soc_ver, other, rom_info) = struct.unpack('<IIII', rsp_data[8:])
        print("ROM Version: 0x%08x" % rom_ver)
        #print("SOC Version: 0x%08x" % soc_ver)
        #print("Life Cycle: 0x%02x" % (other & 0xFF))
        #print("ROM Info: 0x%08x" % rom_info)
        #self.parse_feature_bits(rom_info)
        return rom_ver, (other & 0xFF)
        
    def query_rte_periph(self):
        ret, rsp_data = self.query_rte(0x01, 32)
        if ret != HPM_OK:
            return HPM_ERROR
        print(rsp_data)
        print(len(rsp_data))
        (info, mask, rate) = struct.unpack('<III', rsp_data[8:20])
        print("Periph Info: 0x%08x" % info)
        print("Periph Mask: 0x%08x" % mask)
        print("Periph Rate: 0x%d" % rate)

    def query_rte_last(self):
        ret, rsp_data = self.query_rte(0x03, 12)
        if ret != HPM_OK:
            return HPM_ERROR
        print(rsp_data)
        print(len(rsp_data))
        (boot_mode, boot_word, reserve) = struct.unpack('<BBH', rsp_data[8:12])
        print("Boot Word: 0x%02x" % boot_word)
        print("Boot Mode: 0x%02x" % boot_mode)

    def query_rte_storage(self): 
        ret, rsp_data = self.query_rte(0x04, 36)
        if ret != HPM_OK:
            return HPM_ERROR
        print(rsp_data)
        print(len(rsp_data))
        (info_word, info_type, reserve) = struct.unpack('<BBH', rsp_data[8:12])
        print("Storage info_type: 0x%02x" % info_type)
        print("Storage info_word: 0x%02x" % info_word)

    def configure_memory(self, xip_num, config_addr):
        """ config memory address in target """
        cmd_data = struct.pack(b'<II', xip_num, config_addr)
        ret, rsp_data = self.command(self.HPM_CONFIG_MEMORY, 0x02, 0x00, cmd_data, timeout=SYNC_TIMEOUT)
        if ret != HPM_OK or len(rsp_data) < 8:
            return HPM_ERROR
        (cmd_flag, cmd_num, cmd_type, cmd_null, cmd_ret) = struct.unpack('<BBBBI', rsp_data)
        if cmd_flag != self.HPM_CONFIG_MEMORY or cmd_ret != 0x00:
            return HPM_ERROR
        return HPM_OK

    def write_memory(self, ram_flag, ram_addr, data_len, data_buf):
        """ write memory address in target """
        cmd_data = struct.pack(b'<III', ram_addr, data_len, ram_flag) + data_buf
        ret, rsp_data = self.command(self.HPM_WRITE_MEMORY, 0x03, 0x00, cmd_data, timeout=SYNC_TIMEOUT)
        if ret != HPM_OK or len(rsp_data) < 8:
            return HPM_ERROR
        (cmd_flag, cmd_num, cmd_type, cmd_null, cmd_ret) = struct.unpack('<BBBBI', rsp_data)
        if cmd_flag != self.HPM_WRITE_MEMORY or cmd_ret != 0x00:
            return HPM_ERROR
        return HPM_OK

    def read_memory(self, ram_flag, ram_addr, data_len, timeout=READ_TIMEOUT):
        """ read memory address in target """
        data_buf = b''
        cmd_data = struct.pack(b'<III', ram_addr, data_len, ram_flag)
        ret, rsp_data = self.command(self.HPM_READ_MEMORY, 0x03, 0x00, cmd_data, timeout=SYNC_TIMEOUT)
        if ret != HPM_OK or len(rsp_data) < 8:
            return HPM_ERROR, b''
        (cmd_flag, cmd_num, cmd_type, cmd_null, cmd_ret) = struct.unpack('<BBBBI', rsp_data)
        if cmd_flag != self.HPM_READ_MEMORY or cmd_ret != 0x00:
            return HPM_ERROR, b''
        repe_data_len = data_len
        while repe_data_len:
            data_num = repe_data_len if repe_data_len < 508 else 508
            if self._is_hid:
                uart_num = data_num + 4 + 4
            else:
                uart_num = data_num + 4 + 6
            
            p = self.read(uart_num)
            if len(p) < uart_num:
                continue
            (rs_head, rs_type, rs_len, rs_cmd, cmd_num, cmd_type, cmd_null) = struct.unpack('<BBHBBBB', p[:8])
            if rs_type != self.HPM_DATA_TYPE or rs_cmd != self.HPM_READ_MEMORY:
                return HPM_ERROR, b''
            
            if self._is_hid:
                rs_data = p[8:] # keep op code and delete crc16
            else:
                rs_data = p[8:-2] # keep op code and delete crc16
            if len(rs_data) != data_num:
                return HPM_ERROR, b''
            data_buf += rs_data

            if self._is_hid:
                head_data = self.HPM_USB_SEND_HEAD
            else:
                head_data = self.HPM_START_HEAD
            
            ack = struct.pack(b'<BB', head_data, self.HPM_RECV_ACK)
            self.write(ack)
            repe_data_len -= len(rs_data)
        return HPM_OK, data_buf

    def load_image(self, data_buf):
        print(len(data_buf))
        """ load image address in target """
        ret, rsp_data = self.command(self.HPM_LOAD_IMAGE, 0x00, 0x01, data_buf, timeout=SYNC_TIMEOUT)
        if ret != HPM_OK or len(rsp_data) < 4:
            return HPM_ERROR
        (cmd_flag, cmd_num, cmd_type, cmd_null, cmd_ret) = struct.unpack('<BBBBI', rsp_data)
        if cmd_flag != self.HPM_LOAD_IMAGE or cmd_ret != 0x00:
            return HPM_ERROR
        print(" ")
        return HPM_OK
    
    def rom_reset(self):
        """ write memory address in target """
        cmd_data = struct.pack(b'<I',0x00000000)
        ret, rsp_data = self.command(self.HPM_RESET, 0x01, 0x00, cmd_data, timeout=SYNC_TIMEOUT)
        if ret != HPM_OK or len(rsp_data) < 8:
            return HPM_ERROR
        (cmd_flag, cmd_num, cmd_type, cmd_null, cmd_ret) = struct.unpack('<BBBBI', rsp_data)
        if cmd_flag != self.HPM_RESET or cmd_ret != 0x00:
            return HPM_ERROR
        return HPM_OK
    
    def write_reg(self, addr, value, mask=0xFFFFFFFF, delay_us=0, delay_after_us=0):
        pass

    def update_reg(self, addr, mask, new_val):
        pass

    def mem_begin(self, size, blocks, blocksize, offset):
        pass

    def mem_block(self, data, seq):
        pass

    def mem_finish(self, entrypoint=0):
        pass

    """ Read SPI flash manufacturer and device id """
    def flash_id(self):
        pass

    def build_flash_configuration(self, arg):        
        flash_probe_type = FLASH_PROBE_TYPE.get(arg.flash_type)
        flash_pad_power_on = FLASH_PAD_AFTER.get(arg.pad_power_on)
        flash_pad_config = FLASH_PAD_AFTER.get(arg.pad_config)
        flash_qes = FLASH_QES.get(arg.flash_qes)
        flash_misc = FLASH_MISC.get(arg.flash_misc)
        flash_freq = FLASH_FREQ.get(arg.flash_freq)
        flash_voltage = FLASH_VOLTAGE.get(arg.flash_voltage)
        flash_group = FLASH_GROUP.get(arg.flash_group)
        flash_selection = FLASH_SELECTION.get(arg.flash_selection)
        flash_drive_strength = arg.flash_strength
        flash_size = FLASH_SIZE.get(arg.flash_option)
        flash_option_words = 0

        flash_config =  [
            HPM_IMAGE_MAGIC << 16,
            (flash_probe_type << 28) | (flash_pad_power_on << 24) |(flash_pad_config << 20) | \
            (flash_qes << 16) | (flash_misc << 4) | (flash_freq << 0),
            (flash_voltage << 16) | (flash_group << 12) | (flash_selection << 8) | (flash_drive_strength << 0),
            flash_size,
        ]
        if flash_config[1] != 0 and flash_config[2] != 0 and flash_config[3] != 0:
            flash_option_words = 3
        elif flash_config[1] != 0 and flash_config[2] != 0:
            flash_option_words = 2
        elif flash_config[1] != 0:
            flash_option_words = 1

        flash_config[0] |= flash_option_words
        print("FLASH_CONFIG: [0x%08x, 0x%08x, 0x%08x, 0x%08x]" % (flash_config[0], flash_config[1], flash_config[2], flash_config[3]))
        return flash_config

    @classmethod
    def parse_flash_size_arg(cls, arg):
        pass

    @classmethod
    def parse_flash_freq_arg(cls, arg):
        pass

    def run_stub(self, stub=None):
        pass

    @stub_and_hpm_function_only
    def change_baud(self, baud):
        pass

    def erase_flash(self):
        # depending on flash chip model the erase may take this long (maybe longer!)
        pass

    def erase_region(self, offset, size):
        if offset % self.FLASH_SECTOR_SIZE != 0:
            raise FatalError("Offset to erase from must be a multiple of 4096")
        if size % self.FLASH_SECTOR_SIZE != 0:
            raise FatalError("Size of data to erase must be a multiple of 4096")
        timeout = timeout_per_mb(ERASE_REGION_TIMEOUT_PER_MB, size)
        pass

    def read_flash_slow(self, offset, length, progress_fn):
        pass

    def read_flash(self, offset, length, progress_fn=None):
        if not self.IS_STUB:
            return self.read_flash_slow(offset, length, progress_fn)  # ROM-only routine
        pass

    def flash_set_parameters(self, args, xip_num):
        FLASH_CONFIG = self.build_flash_configuration(args)
        config_bytes = struct.pack('<4I', *FLASH_CONFIG)
        self.write_memory(self.SRAM_FLAG, self.FLASH_CONF_ADDR, len(config_bytes), config_bytes)
        self.configure_memory(xip_num, self.FLASH_CONF_ADDR)

    def _flash_addr(self, addr, xip_num):
        XPI_BASE = self.XPI0_START if xip_num == self.XPI0_FLAG else self.XPI1_START
        return addr if addr >= XPI_BASE else XPI_BASE + addr

    def flash_set_file(self, arg, xip_num):
        for addr_obj, file_obj in arg.addr_filename:
            # 读取文件内容
            file_content = file_obj.read()
            # 使用 self.write_memory 发送数据
            flash_addr = self._flash_addr(addr_obj, xip_num)
            print("xip_num: %d, Write files to Flash: %s, addr: 0x%08x, size: %d" % (xip_num, file_obj.name, flash_addr, len(file_content)))
            self.write_memory(xip_num, flash_addr, len(file_content), file_content)
            print(' OK!')

    def flash_get_file(self, arg, xip_num):
        flash_addr = self._flash_addr(arg.address, xip_num)
        ret, rs_data = self.read_memory(xip_num, flash_addr, arg.size)
        if ret == HPM_OK:
            with open(arg.filename, 'wb') as file:
                file.write(rs_data)
        else:
            print('Failed to get file from Flash !!!')

    def get_crystal_freq(self):
        pass

    def hard_reset(self):
        print('Hard resetting via RTS pin...')
        self._setRTS(True)  # EN->LOW
        time.sleep(0.1)
        self._setRTS(False)

    def soft_reset(self, stay_in_bootloader):
        pass


class HPMROM(HPMLoader):
    """Access class for HPM ROM bootloader
    """
    CHIP_NAME = "HPM-NULL"
    IS_STUB = False

    SRAM_FLAG = 0
    XPI0_FLAG = 0x10000
    XPI1_FLAG = 0x10001
    OTP_FLAG  = 0x20000

    FLASH_MAGIC_VALUE = 0xfcf9

    def is_flash_encryption_key_valid(self):
        pass

    def get_flash_crypt_config(self):
        pass

    def get_encrypted_download_disabled(self):
        pass

    def get_pkg_version(self):
        pass

    def get_chip_full_revision(self):
        pass

    def get_chip_revision(self):
        return self.get_minor_chip_version()

    def get_minor_chip_version(self):
        pass

    def get_major_chip_version(self):
        pass

    def get_chip_description(self):
        pass

    def get_chip_features(self):
        pass

    def read_efuse(self, n):
        pass

    def get_crystal_freq(self):
        return 24

class HPM6700ROM(HPMROM):
    CHIP_NAME = "HPM6700"
    USB_VID = 0x34b7
    USB_PID = 0x0001
    FLASH_CONF_ADDR = 0x200
    CPU0_ILM_START = 0x00000000
    CPU0_ILM_END   = 0x0003FFFF
    CPU0_DLM_START = 0x00080000
    CPU0_DLM_END   = 0x000BFFFF
    XPI0_START     = 0x80000000
    XPI0_END       = 0x9FFFFFFF
    XPI1_START     = 0x90000000
    XPI1_END       = 0x9FFFFFFF

    MEMORY_MAP = [[0x00000000, 0x0001FFFF, "ILM"],
                  [0x00080000, 0x0009FFFF, "DLM"],
                  [0x80000000, 0x8FFFFFFF, "XPI0"],
                  [0x90000000, 0x9FFFFFFF, "XPI1"],
                  [0xF0400000, 0xF0407FFF, "HRAM"]]

    def get_chip_description(self):
        chip_name = {
            0: "HPM6700",
        }.get(self.get_pkg_version(), "unknown HPM6700")
        major_rev = self.get_major_chip_version()
        minor_rev = self.get_minor_chip_version()
        return "%s (revision v%d.%d)" % (chip_name, major_rev, minor_rev)

    def get_chip_features(self):
        pass

    def eth_mac(self):
        pass

class HPM6300ROM(HPMROM):
    CHIP_NAME = "HPM6300"
    USB_VID = 0x34b7
    USB_PID = 0x0002
    FLASH_CONF_ADDR = 0x200
    CPU0_ILM_START = 0x00000000
    CPU0_ILM_END   = 0x0001FFFF
    CPU0_DLM_START = 0x00080000
    CPU0_DLM_END   = 0x0009FFFF
    XPI0_START     = 0x80000000
    XPI0_END       = 0x8FFFFFFF

    MEMORY_MAP = [[0x00000000, 0x0001FFFF, "ILM"],
                  [0x00080000, 0x0009FFFF, "DLM"],
                  [0x80000000, 0x8FFFFFFF, "XPI0"],
                  [0xF0400000, 0xF0407FFF, "HRAM"]]

    def get_chip_description(self):
        chip_name = {
            0: "HPM6300",
        }.get(self.get_pkg_version(), "unknown HPM6300")
        major_rev = self.get_major_chip_version()
        minor_rev = self.get_minor_chip_version()
        return "%s (revision v%d.%d)" % (chip_name, major_rev, minor_rev)

    def get_chip_features(self):
        pass

class HPM6200ROM(HPMROM):
    CHIP_NAME = "HPM6200"
    USB_VID = 0x34b7
    USB_PID = 0x0003
    FLASH_CONF_ADDR = 0x200
    CPU0_ILM_START = 0x00000000
    CPU0_ILM_END   = 0x0001FFFF
    CPU1_ILM_START = 0x00020000
    CPU1_ILM_END   = 0x0003FFFF
    CPU0_DLM_START = 0x00080000
    CPU0_DLM_END   = 0x0009FFFF
    CPU1_DLM_START = 0x000A0000
    CPU1_DLM_END   = 0x000BFFFF
    XPI0_START     = 0x80000000
    XPI0_END       = 0x8FFFFFFF

    MEMORY_MAP = [[0x00000000, 0x0001FFFF, "ILM"],
                  [0x00080000, 0x0009FFFF, "DLM"],
                  [0x80000000, 0x8FFFFFFF, "XPI0"],
                  [0xF0400000, 0xF0407FFF, "HRAM"]]

    def get_chip_description(self):
        chip_name = {
            0: "HPM6200",
        }.get(self.get_pkg_version(), "unknown HPM6200")
        major_rev = self.get_major_chip_version()
        minor_rev = self.get_minor_chip_version()
        return "%s (revision v%d.%d)" % (chip_name, major_rev, minor_rev)

    def get_chip_features(self):
        pass

class HPM6800ROM(HPMROM):
    CHIP_NAME = "HPM5300"
    USB_VID = 0x34b7
    USB_PID = 0x0004
    FLASH_CONF_ADDR = 0x200
    CPU0_ILM_START = 0x00000000
    CPU0_ILM_END   = 0x0001FFFF
    CPU0_DLM_START = 0x00080000
    CPU0_DLM_END   = 0x0009FFFF
    XPI0_START     = 0x80000000
    XPI0_END       = 0x8FEFFFFF

    MEMORY_MAP = [[0x00000000, 0x0001FFFF, "ILM"],
                  [0x00080000, 0x0009FFFF, "DLM"],
                  [0x80000000, 0x8FEFFFFF, "XPI0"],
                  [0xF0400000, 0xF0407FFF, "HRAM"]]

    def get_chip_description(self):
        chip_name = {
            0: "HPM5800",
        }.get(self.get_pkg_version(), "unknown HPM6800")
        major_rev = self.get_major_chip_version()
        minor_rev = self.get_minor_chip_version()
        return "%s (revision v%d.%d)" % (chip_name, major_rev, minor_rev)

    def get_chip_features(self):
        pass

class HPM5300ROM(HPMROM):
    CHIP_NAME = "HPM5300"
    OPTION = [0xfcf90002, 0x00000005, 0x1000, 0]
    USB_VID = 0x34b7
    USB_PID = 0x0005
    FLASH_CONF_ADDR = 0x200
    CPU0_ILM_START = 0x00000000
    CPU0_ILM_END   = 0x0001FFFF
    CPU0_DLM_START = 0x00080000
    CPU0_DLM_END   = 0x0009FFFF
    XPI0_START     = 0x80000000
    XPI0_END       = 0x8FFFFFFF

    MEMORY_MAP = [[0x00000000, 0x0001FFFF, "ILM"],
                  [0x00080000, 0x0009FFFF, "DLM"],
                  [0x80000000, 0x8FFFFFFF, "XPI0"],
                  [0xF0400000, 0xF0407FFF, "HRAM"]]

    def get_chip_description(self):
        chip_name = {
            0: "HPM5300",
        }.get(self.get_pkg_version(), "unknown HPM5300")
        major_rev = self.get_major_chip_version()
        minor_rev = self.get_minor_chip_version()
        return "%s (revision v%d.%d)" % (chip_name, major_rev, minor_rev)

    def get_chip_features(self):
        pass

class HPM6e00ROM(HPMROM):
    CHIP_NAME = "HPM6e00"
    USB_VID = 0x34b7
    USB_PID = 0x0006
    FLASH_CONF_ADDR = 0x200
    CPU0_ILM_START = 0x00000000
    CPU0_ILM_END   = 0x0003FFFF
    CPU1_ILM_START = 0x00040000
    CPU1_ILM_END   = 0x0007FFFF
    CPU0_DLM_START = 0x00200000
    CPU0_DLM_END   = 0x0023FFFF
    CPU1_DLM_START = 0x00240000
    CPU1_DLM_END   = 0x0027FFFF
    XPI0_START     = 0x80000000
    XPI0_END       = 0x8FFFFFFF

    MEMORY_MAP = [[0x00000000, 0x0003FFFF, "ILM"],
                  [0x00080000, 0x0009FFFF, "DLM"],
                  [0x80000000, 0x8FFFFFFF, "XPI0"],
                  [0xF0200000, 0xF0207FFF, "HRAM"]]

    def get_chip_description(self):
        chip_name = {
            0: "HPM6e00",
        }.get(self.get_pkg_version(), "unknown HPM6e00")
        major_rev = self.get_major_chip_version()
        minor_rev = self.get_minor_chip_version()
        return "%s (revision v%d.%d)" % (chip_name, major_rev, minor_rev)

    def get_chip_features(self):
        pass

class HPM6p00ROM(HPMROM):
    CHIP_NAME = "HPM6p00"
    USB_VID = 0x34b7
    USB_PID = 0x0007
    FLASH_CONF_ADDR = 0x200
    CPU0_ILM_START = 0x00000000
    CPU0_ILM_END   = 0x0001FFFF
    CPU1_ILM_START = 0x00040000
    CPU1_ILM_END   = 0x0005FFFF
    CPU0_DLM_START = 0x00200000
    CPU0_DLM_END   = 0x0021FFFF
    CPU1_DLM_START = 0x00240000
    CPU1_DLM_END   = 0x0025FFFF
    XPI0_START     = 0x80000000
    XPI0_END       = 0x8FFFFFFF

    MEMORY_MAP = [[0x00000000, 0x0001FFFF, "ILM"],
                  [0x00080000, 0x0009FFFF, "DLM"],
                  [0x80000000, 0x8FFFFFFF, "XPI0"],
                  [0xF0200000, 0xF0207FFF, "HRAM"]]

    def get_chip_description(self):
        chip_name = {
            0: "HPM6p00",
        }.get(self.get_pkg_version(), "unknown HPM6p00")
        major_rev = self.get_major_chip_version()
        minor_rev = self.get_minor_chip_version()
        return "%s (revision v%d.%d)" % (chip_name, major_rev, minor_rev)

    def get_chip_features(self):
        pass

class HPM5e00ROM(HPMROM):
    CHIP_NAME = "HPM5e00"
    USB_VID = 0x34b7
    USB_PID = 0x0008
    FLASH_CONF_ADDR = 0x200
    CPU0_ILM_START = 0x00000000
    CPU0_ILM_END   = 0x0001FFFF
    CPU0_DLM_START = 0x00200000
    CPU0_DLM_END   = 0x0021FFFF
    XPI0_START     = 0x80000000
    XPI0_END       = 0x8FFFFFFF

    MEMORY_MAP = [[0x00000000, 0x0001FFFF, "ILM"],
                  [0x00080000, 0x0009FFFF, "DLM"],
                  [0x80000000, 0x8FFFFFFF, "XPI0"],
                  [0xF0200000, 0xF0207FFF, "HRAM"]]

    def get_chip_description(self):
        chip_name = {
            0: "HPM5e00",
        }.get(self.get_pkg_version(), "unknown HPM5e00")
        major_rev = self.get_major_chip_version()
        minor_rev = self.get_minor_chip_version()
        return "%s (revision v%d.%d)" % (chip_name, major_rev, minor_rev)

    def get_chip_features(self):
        pass

def LoadFirmwareImage(filename):
    with open(filename, 'rb') as fb:
        file_content = fb.read()
    return file_content

def slip_reader(port, trace_function):
    """Generator to read SLIP packets from a serial port.
    Yields one full SLIP packet at a time, raises exception on timeout or invalid data.

    Designed to avoid too many calls to serial.read(1), which can bog
    down on slow systems.
    """
    partial_packet = None
    successful_slip = False
    while True:
        waiting = port.inWaiting()
        read_bytes = port.read(1 if waiting == 0 else waiting)
        if read_bytes == b'':
            if partial_packet is None:  # fail due to no data
                msg = "Serial data stream stopped: Possible serial noise or corruption." if successful_slip else "No serial data received."
                trace_function(msg)
                raise FatalError(msg)
            else:  # fail during packet transfer
                trace_function("Received full packet: %s", HexFormatter(partial_packet))
                yield partial_packet
                partial_packet = None
                successful_slip = True

        trace_function("Read %d bytes: %s", len(read_bytes), HexFormatter(read_bytes))

        for b in read_bytes:
            if type(b) is int:
                b = bytes([b])  # python 2/3 compat
            if partial_packet is None:  # waiting for packet header
                if b == b'\x5A':
                    partial_packet = b'\x5A'
                else:
                    trace_function("Read invalid data: %s", HexFormatter(read_bytes))
                    trace_function("Remaining data in serial buffer: %s", HexFormatter(port.read(port.inWaiting())))
                    raise FatalError('Invalid head of packet (0x%s): Possible serial noise or corruption.' % hexify(b))
            else:  # normal byte in packet
                partial_packet += b 

def arg_auto_int(x):
    return int(x, 0)


def format_chip_name(c):
    """ Normalize chip name from user input """
    c = c.lower().replace('-', '')
    return c


def div_roundup(a, b):
    """ Return a/b rounded up to nearest integer,
    equivalent result to int(math.ceil(float(int(a)) / float(int(b))), only
    without possible floating point accuracy errors.
    """
    return (int(a) + int(b) - 1) // int(b)


def align_file_position(f, size):
    """ Align the position in the file to the next block of specified size """
    align = (size - 1) - (f.tell() % size)
    f.seek(align, 1)


def flash_size_bytes(size):
    """ Given a flash size of the type passed in args.flash_size
    (ie 512KB or 1MB) then return the size in bytes.
    """
    if "MB" in size:
        return int(size[:size.index("MB")]) * 1024 * 1024
    elif "KB" in size:
        return int(size[:size.index("KB")]) * 1024
    else:
        raise FatalError("Unknown size %s" % size)


def hexify(s, uppercase=True):
    format_str = '%02X' if uppercase else '%02x'
    if not PYTHON2:
        return ''.join(format_str % c for c in s)
    else:
        return ''.join(format_str % ord(c) for c in s)

class HexFormatter(object):
    """
    Wrapper class which takes binary data in its constructor
    and returns a hex string as it's __str__ method.

    This is intended for "lazy formatting" of trace() output
    in hex format. Avoids overhead (significant on slow computers)
    of generating long hex strings even if tracing is disabled.

    Note that this doesn't save any overhead if passed as an
    argument to "%", only when passed to trace()

    If auto_split is set (default), any long line (> 16 bytes) will be
    printed as separately indented lines, with ASCII decoding at the end
    of each line.
    """
    def __init__(self, binary_string, auto_split=True):
        self._s = binary_string
        self._auto_split = auto_split

    def __str__(self):
        if self._auto_split and len(self._s) > 16:
            result = ""
            s = self._s
            while len(s) > 0:
                line = s[:16]
                ascii_line = "".join(c if (c == ' ' or (c in string.printable and c not in string.whitespace))
                                     else '.' for c in line.decode('ascii', 'replace'))
                s = s[16:]
                result += "\n    %-16s %-16s | %s" % (hexify(line[:8], False), hexify(line[8:], False), ascii_line)
            return result
        else:
            return hexify(self._s, False)


def pad_to(data, alignment, pad_character=b'\xFF'):
    """ Pad to the next alignment boundary """
    pad_mod = len(data) % alignment
    if pad_mod != 0:
        data += pad_character * (alignment - pad_mod)
    return data


class FatalError(RuntimeError):
    def __init__(self, message):
        RuntimeError.__init__(self, message)

    @staticmethod
    def WithResult(message, result):
        """
        Return a fatal error object that appends the hex values of
        'result' and its meaning as a string formatted argument.
        """

        err_defs = {
            0x101: 'Out of memory',
            0x102: 'Invalid argument',
            0x103: 'Invalid state',
            0x104: 'Invalid size',
            0x105: 'Requested resource not found',
            0x106: 'Operation or feature not supported',
            0x107: 'Operation timed out',
            0x108: 'Received response was invalid',
            0x109: 'CRC or checksum was invalid',
            0x10A: 'Version was invalid',
            0x10B: 'MAC address was invalid',
            # Flasher stub error codes
            0xC000: 'Bad data length',
            0xC100: 'Bad data checksum',
            0xC200: 'Bad blocksize',
            0xC300: 'Invalid command',
            0xC400: 'Failed SPI operation',
            0xC500: 'Failed SPI unlock',
            0xC600: 'Not in flash mode',
            0xC700: 'Inflate error',
            0xC800: 'Not enough data',
            0xC900: 'Too much data',
            0xFF00: 'Command not implemented',
        }

        err_code = struct.unpack(">H", result[:2])
        message += " (result was {}: {})".format(hexify(result), err_defs.get(err_code[0], 'Unknown result'))
        return FatalError(message)


class NotImplementedInROMError(FatalError):
    """
    Wrapper class for the error thrown when a particular HPM bootloader function
    is not implemented in the ROM bootloader.
    """
    def __init__(self, bootloader, func):
        FatalError.__init__(self, "%s ROM does not support function %s." % (bootloader.CHIP_NAME, func.__name__))


class NotSupportedError(FatalError):
    def __init__(self, hpm, function_name):
        FatalError.__init__(self, "Function %s is not supported for %s." % (function_name, hpm.CHIP_NAME))

# "Operation" commands, executable at command line. One function each
#
# Each function takes either two args (<HPMLoader instance>, <args>) or a single <args>
# argument.


class UnsupportedCommandError(RuntimeError):
    """
    Wrapper class for when ROM loader returns an invalid command response.

    Usually this indicates the loader is running in Secure Download Mode.
    """
    def __init__(self, hpm, op):
        if hpm.secure_download_mode:
            msg = "This command (0x%x) is not supported in Secure Download Mode" % op
        else:
            msg = "Invalid (unsupported) command 0x%x" % op
        RuntimeError.__init__(self, msg)

def eth_mac(hpm, args):
    if hasattr(hpm, 'eth_mac'):
        mac = hpm.eth_mac()
        def print_mac(label, mac):
            print('%s: %s' % (label, ':'.join(map(lambda x: '%02x' % x, mac))))
        print_mac("MAC", mac)
    else:
        print('Warning: %s has no ETH MAC address.' % hpm.CHIP_NAME)

def load_image(hpm, bootName):
    image = LoadFirmwareImage(bootName)
    hpm.load_image(image)
    print('RAM boot...')

def rom_reset(hpm, args):
    hpm.rom_reset()
    print('reset...')
    time.sleep(1)

def write_mem(hpm, args):
    pass

def read_mem(hpm, args):
    print(args)
    try:
        ret, rs_data = hpm.read_memory(hpm.SRAM_FLAG, args.address, 4)
        if ret != HPM_OK or len(rs_data) != 4:
            raise FatalError.WithResult("Failed to read SRAM", rs_data)

        print(format_word_hex(rs_data))
        #ram_word = struct.unpack('<I', rs_data)
        #print('SRAM addr: 0x%08x, data: 0x%08x' % (args.address, ram_word))
    except NotSupportedError:
        print('Warning: %s has no data.' % hpm.CHIP_NAME)

def dump_mem(hpm, args):
    with open(args.filename, 'wb') as f:
        for i in range(args.size // 4):
            #d = hpm.read_mem(args.address + (i * 4))
            #f.write(struct.pack(b'<I', d))
            if f.tell() % 1024 == 0:
                print_overwrite('%d bytes read... (%d %%)' % (f.tell(),
                                                              f.tell() * 100 // args.size))
            sys.stdout.flush()
        print_overwrite("Read %d bytes" % f.tell(), last_line=True)
    print('Done!')

def write_flash(hpm, args):
    print('Writing flash...')
    hpm.flash_set_parameters(args, hpm.XPI0_FLAG)
    hpm.flash_set_file(args, hpm.XPI0_FLAG)
    if args.verify:
        print('Verifying just-written flash...')
        print('(This option is deprecated, flash contents are now always read back after flashing.)')
        verify_flash(hpm, args)
    print('Done!')

def read_flash(hpm, args):
    print('Reading Flash...')
    hpm.flash_set_parameters(args, hpm.XPI0_FLAG)
    hpm.flash_get_file(args, hpm.XPI0_FLAG)
    print('Done!')

def image_info(args):
    if args.chip == "auto":
        print("WARNING: --chip not specified, defaulting to HPM5300.")
    pass

def chip_id(hpm, args):
    try:
        ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, 64, 4)
        if ret != HPM_OK or len(rs_data) != 4:
            raise FatalError.WithResult("Failed to read chip ID", rs_data)
        otp_word = struct.unpack('<I', rs_data)
        print('Chip ID: 0x%08x' % otp_word)
    except NotSupportedError:
        print('Warning: %s has no Chip ID.' % hpm.CHIP_NAME)

def erase_flash(hpm, args):
    print('Erasing flash (this may take a while)...')
    t = time.time()
    hpm.erase_flash()
    print('Chip erase completed successfully in %.1fs' % (time.time() - t))

def erase_region(hpm, args):
    print('Erasing region (may be slow depending on size)...')
    t = time.time()
    hpm.erase_region(args.address, args.size)
    print('Erase completed successfully in %.1f seconds.' % (time.time() - t))

def verify_flash(hpm, args):
    pass

def otp_dump(hpm, args):
    print('Summary OTP...')
    ret, lock_data = hpm.read_memory(hpm.OTP_FLAG, 0, 4)
    if ret != HPM_OK or len(lock_data) < 4:
        return HPM_ERROR, b''
    
    lock_value = int.from_bytes(lock_data, byteorder='little')

    ret, head0_data = hpm.read_memory(hpm.OTP_FLAG, 1, 32 + 16 - 4)
    if ret != HPM_OK or len(head0_data) < 44:
        return HPM_ERROR, b''
    
    if lock_value & 0x00000008: # Debug lock bit
        debugkey_data = b'\x00' * 16
    else:
        ret, debugkey_data = hpm.read_memory(hpm.OTP_FLAG, 12, 16)
        if ret != HPM_OK or len(debugkey_data) < 16:
            return HPM_ERROR, b''
    
    ret, other_data = hpm.read_memory(hpm.OTP_FLAG, 16, 80 * 4)
    if ret != HPM_OK or len(other_data) < 80:
        return HPM_ERROR, b''
    
    if lock_value & 0x01000000: # EXIP KEK0 bit
        exip_kek0_data = b'\x00' * 16
    else:
        ret, exip_kek0_data = hpm.read_memory(hpm.OTP_FLAG, 96, 16)
        if ret != HPM_OK or len(exip_kek0_data) < 16:
            return HPM_ERROR, b''
    if lock_value & 0x02000000: # OTP KEK0 bit
        otp_kek0_data = b'\x00' * 16
    else:
        ret, otp_kek0_data = hpm.read_memory(hpm.OTP_FLAG, 100, 16)
        if ret != HPM_OK or len(otp_kek0_data) < 16:
            return HPM_ERROR, b''
    if lock_value & 0x04000000: # EXIP KEK1 bit
        exip_kek1_data = b'\x00' * 16
    else:
        ret, exip_kek1_data = hpm.read_memory(hpm.OTP_FLAG, 104, 16)
        if ret != HPM_OK or len(exip_kek1_data) < 16:
            return HPM_ERROR, b''
    if lock_value & 0x08000000: # OTP KEK1 bit
        otp_kek1_data = b'\x00' * 16
    else:
        ret, otp_kek1_data = hpm.read_memory(hpm.OTP_FLAG, 108, 16)
        if ret != HPM_OK or len(otp_kek1_data) < 16:
            return HPM_ERROR, b'' 
    if lock_value & 0x30000000: # MK bit
        otp_mk_data = b'\x00' * 32
    else:
        ret, otp_mk_data = hpm.read_memory(hpm.OTP_FLAG, 112, 32)
        if ret != HPM_OK or len(otp_mk_data) < 16:
            return HPM_ERROR, b''
        
    if lock_value & 0xc0000000: # 
        otp_uk_data = b'\x00' * 32
    else:
        ret, otp_uk_data = hpm.read_memory(hpm.OTP_FLAG, 120, 32)
        if ret != HPM_OK or len(otp_uk_data) < 16:
            return HPM_ERROR, b''
    # 拼接所有数据
    rs_data = lock_data + head0_data + debugkey_data + other_data + exip_kek0_data + otp_kek0_data + exip_kek1_data + otp_kek1_data + otp_mk_data + otp_uk_data
    print(format_word_hex(rs_data))
    return HPM_OK, rs_data

def is_locked(hard_lock, word_index):
    """判断给定 word 是否被锁定"""
    bit_index = word_index // 4
    return ((hard_lock >> bit_index) & 1) == 1

def print_key_block(name, word_list, hard_lock, otp_word):
    if is_locked(hard_lock, word_list[0]):
        print(f"{name:<18}: ----------, ----------, ----------, ----------")
    else:
        print(f"{name:<18}: 0x%08x, 0x%08x, 0x%08x, 0x%08x" %
              (otp_word[word_list[0]],
               otp_word[word_list[1]],
               otp_word[word_list[2]],
               otp_word[word_list[3]]))

def otp_summary(hpm, args):
    ret, rs_data = otp_dump(hpm, args)
    if ret != HPM_OK or len(rs_data) !=  512:
        print('===============================================================')
        print('Failed to read OTP summary, please check if the chip is locked.')
        print('===============================================================')
        return
    
    otp_word = struct.unpack('<128I', rs_data) 
    print('======================== OTP Summary ========================')
    print('HARD_LOCK         : 0x%08x'% otp_word[0])
    print('LIFECYCLE         : 0x%01x'% (((otp_word[1] >> 28) & 0xF) | (otp_word[1] & 0xF)))
    print('TCU_DISABLE       : %d'% ((otp_word[1] >> 19) & 0x01))
    print('DEBUG_DISABLE     : %d'% ((otp_word[1] >> 17) & 0x01))
    print('PUK_REVOKE        : 0x%02x'%( (otp_word[1] >> 8) & 0xFF))
    print('MONO_EPOCH        : 0x%04x'% ((otp_word[2] >> 16) & 0xFFFF))
    print('EXIP1_RESTRICT    : %d'% ((otp_word[2] >> 1) & 0x01))
    print('EXIP0_RESTRICT    : %d'% ((otp_word[2] >> 0) & 0x01))
    print('SW_VER            : 0x%08x'% otp_word[3])
    print('32K trim          :0x%08x'% otp_word[4])
    print('24M/PMCCAP/DCDC trim :0x%08x'% otp_word[5])
    print('USER CODE :0x%08x'% otp_word[7])
    # 4~7  4Word
    print('DIE_TRACE         : 0x%08x, 0x%08x, 0x%08x, 0x%08x'% (otp_word[8],otp_word[9],otp_word[10],otp_word[11]))
    print_key_block('DEBUG_KEY',     [12, 13, 14, 15], otp_word[0], otp_word)
    # 16~20 5Word
    print('TSNS_BASE         : 0x%04x'% ((otp_word[21]) & 0xFFFF))
    print('TSNS_SLOPE        : 0x%04x'% ((otp_word[21] >> 16) & 0xFFFF))
    # 22~23 n Word
    print('XPI_FREQ_OPTION   : 0x%01x'% ((otp_word[24] >> 0) & 0xF))
    print('XPI_INSTANCE      : %d'%     ((otp_word[24] >> 4) & 0x1))
    print('XPI_PIN_GROUP     : %d'%     ((otp_word[24] >> 5) & 0x1))
    print('XPI_PORT_SEL      : %d'%     ((otp_word[24] >> 6) & 0x3))
    print('PROBE_TYPE        : 0x%01x'% ((otp_word[24] >> 8) & 0xF))
    print('ENCRYPT_XIP       : %d'% ((otp_word[24] >> 12) & 0x1))
    print('XPI_NOR_CFG_SRC   : %d'% ((otp_word[24] >> 13) & 0x1))
    print('XPI_DEFAULT_READ  : %d'% ((otp_word[24] >> 14) & 0x3))
    print('BOOT_MODE         : 0x%01x'%  ((otp_word[24] >> 16) & 0xF))
    print('DRIVE_STRENGTH    : 0x%01x'%  ((otp_word[24] >> 20) & 0xF))
    print('DUMMY_CYCLE       : 0x%01x'%  ((otp_word[24] >> 24) & 0xFF))
    print('SEC_IMG_OFFSET    : 0x%04x'% ((otp_word[25]) & 0xFFFF))
    print('MAX_IMG_LEN       : 0x%04x'% ((otp_word[25] >> 16) & 0xFFFF))
    print('FORCE_COLD_BOOT   : %d'% ((otp_word[26] >> 0) & 0x3))
    print('FORCE_WAKEUP_ENTRY_CHK :%d'% ((otp_word[26] >> 2) & 0x3))
    print('HIGH_SPEED_BOOT   : %d'% ((otp_word[26] >> 4) & 0x1))
    # 26[BIT5~BIT31] 27 bit
    # 27 1 Word
    # 28~63 36 Word
    print('CHIP_ID           : 0x%08x'% otp_word[64])
    # 65~66 2 Word
    print('USB_VID           : 0x%04x'% ((otp_word[67]) & 0xFFFF))
    print('USB_PID           : 0x%04x'% ((otp_word[67] >> 16) & 0xFFFF))
    # 68 1 Word
    print('USER_OTP          : 0x%08x, 0x%08x, 0x%08x, 0x%08x'% (otp_word[69],otp_word[70],otp_word[71],otp_word[72]))
    print('                  : 0x%08x, 0x%08x, 0x%08x, 0x%08x'% (otp_word[73],otp_word[74],otp_word[75],otp_word[76]))
    print('                  : 0x%08x, 0x%08x, 0x%08x'% (otp_word[77],otp_word[78],otp_word[79]))

    print('PUBLIC_KEY_HASH   : 0x%08x, 0x%08x, 0x%08x, 0x%08x'% (otp_word[80],otp_word[81],otp_word[82],otp_word[83]))
    print('                  : 0x%08x, 0x%08x, 0x%08x, 0x%08x'% (otp_word[84],otp_word[85],otp_word[85],otp_word[87]))
    print('UUID              : 0x%08x, 0x%08x, 0x%08x, 0x%08x'% (otp_word[88],otp_word[89],otp_word[90],otp_word[91]))

    print_key_block('EXIP0_KEY',     [96, 97, 98, 99], otp_word[0], otp_word)
    print_key_block('OTP_KEK0',      [100, 101, 102, 103], otp_word[0], otp_word)
    print_key_block('EXIP1_KEY',     [104, 105, 106, 107], otp_word[0], otp_word)
    print_key_block('OTP_KEK1',      [108, 109, 110, 111], otp_word[0], otp_word)
    print_key_block('MASTER_KEY',    [112, 113, 114, 115], otp_word[0], otp_word)
    print_key_block('          ', [116, 117, 118, 119], otp_word[0], otp_word)
    # 120~127 8 Word


def read_csv(file_path):
    """
    读取 CSV 文件并返回一个字典，键为 word，值为 value。
    """
    # 检查文件是否存在
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    data = {}
    with open(file_path, 'r', encoding='utf-8') as csvfile:
        reader = csv.reader(csvfile)
        for row in reader:
            # 跳过空行或无效行
            if len(row) != 2:
                print(f"Skipping invalid row: {row}")
                continue
            try:
                word = int(row[0])  # 转换为整数
                value = int(row[1], 16)  # 将十六进制字符串转换为整数
                data[word] = value
            except ValueError as e:
                print(f"Skipping invalid row: {row} ({e})")
    return data

def otp_write(hpm, args):
    print('Write OTP...')
    print(args.otpfile)
    FT_HARD_LOCK = 0
    
    ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, FT_HARD_LOCK, 4)
    if ret != 0:
        print("Error reading OTP at HARD_LOCK")
        return
    
    # 将读取的字节数据转换为整数
    actual_value = int.from_bytes(rs_data, byteorder='little')
    # 打印读取的值
    print(f"OTP at HARD_LOCK: 0x{actual_value:08X}")
    
    csv_data = read_csv(args.otpfile)
    # 遍历 CSV 文件中的每个 word
    for word_addr, expected_value in csv_data.items():
        time.sleep(0.1)
        if not (0 <= word_addr < 128):
            print(f"Error: Invalid word address {word_addr}")
            continue
        
        # 计算该 word 属于哪个 4-word 区域
        bit_index = word_addr // 4
        # 判断该 bit 是否为 1（表示该 4-word 区域已锁定）
        if (actual_value >> bit_index) & 1:
            print(f"Warning: Word {word_addr} is in locked region (bit {bit_index} set in HARD_LOCK), skipping write.")
            continue
        
        # 读取 OTP 数据
        ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, word_addr, 4)  # 每个 word 占 4 字节
        if ret != HPM_OK:
            print(f"Error reading OTP at word {word_addr}")
            continue
        # 将读取的字节数据转换为整数
        actual_value = int.from_bytes(rs_data, byteorder='little')  # 假设是小端字节序
        # 比较值
        if actual_value != expected_value:
            print(f"Mismatch at word {word_addr}: OTP=0x{actual_value:08X}, CSV=0x{expected_value:08X}")
            if actual_value == 0 or word_addr == 0 : 
                print(f"Warning: OTP at word {word_addr} is 0x{actual_value:08X}, which may indicate it is not programmed.")
                # 如果不匹配，尝试写入
                try:
                    hpm.write_memory(hpm.OTP_FLAG, word_addr, 4, expected_value.to_bytes(4, byteorder='little'))
                    print(f"Written OTP at word {word_addr}: 0x{expected_value:08X}")
                except Exception as e:
                    print(f"Error writing OTP at word {word_addr}: {e}")
        else:
            print(f"Word {word_addr} matches: OTP=0x{actual_value:08X}")

def otp_exip_enable(hpm, args):
    print('Write exip key OTP...')
    EXIP_ADDR = 24
    ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, EXIP_ADDR, 4)
    if ret != 0:
        print("Error reading OTP at EXIP_ADDR")
        return
    # 将读取的字节数据转换为整数
    exip_value = int.from_bytes(rs_data, byteorder='little')
    exip_value = exip_value | 0x00001000  # 设置 bit 12 为 1，表示 EXIP 区域已启用
    try:
        hpm.write_memory(hpm.OTP_FLAG, EXIP_ADDR, 4, exip_value.to_bytes(4, byteorder='little'))
        print(f"Written OTP at word {EXIP_ADDR}: 0x{exip_value:08X}")
    except Exception as e:
        print(f"Error writing OTP at word {EXIP_ADDR}: {e}")

def otp_lifecycle_enable(hpm, args):
    print('Write exip key OTP...')
    FT_HARD_LOCK = 0
    
    ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, FT_HARD_LOCK, 4)
    if ret != 0:
        print("Error reading OTP at HARD_LOCK")
        return
    
    # 将读取的字节数据转换为整数
    lock_value = int.from_bytes(rs_data, byteorder='little')
    print(f"OTP at HARD_LOCK: 0x{lock_value:08X}")
    if lock_value != 0x3140001E:
        print("Error: HARD_LOCK value is not 0x3140001E, cannot enable LIFECYCLE_ADDR.")
        return
    
    LIFECYCLE_ADDR = 1
    ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, LIFECYCLE_ADDR, 4)
    if ret != 0:
        print("Error reading OTP at LIFECYCLE_ADDR")
        return
    # 将读取的字节数据转换为整数
    lifecycle_value = int.from_bytes(rs_data, byteorder='little')
    lifecycle_value = lifecycle_value | 0x30000003  # 设置 bit 1 为 1，表示 LIFECYCLE_ADDR 区域已启用
    try:
        hpm.write_memory(hpm.OTP_FLAG, LIFECYCLE_ADDR, 4, lifecycle_value.to_bytes(4, byteorder='little'))
        print(f"Written OTP at word {LIFECYCLE_ADDR}: 0x{lifecycle_value:08X}")
        time.sleep(0.1)
        LIFECYCLE_ADDR = 1
        ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, LIFECYCLE_ADDR, 4)
        if ret != 0:
            print("Error reading OTP at LIFECYCLE_ADDR")
            return
        print("================================================================")
        print("================================================================")
        lifecycle_value = int.from_bytes(rs_data, byteorder='little')
        if lifecycle_value != 0x30000003:
            print("\033[1;31m" + r"""
            ███████  █████  ██ ██      
            ██      ██   ██ ██ ██      
            █████   ███████ ██ ██      
            ██      ██   ██ ██ ██      
            ██      ██   ██ ██ ███████ 
            """ + "\033[0m")
            return
        
        print("\033[1;32m" + r"""
        ██████  █████  ███████ ███████ 
        ██   ██ ██   ██ ██      ██      
        ██████  ███████ █████   █████   
        ██      ██   ██ ██      ██      
        ██      ██   ██ ███████ ███████ 
        """ + "\033[0m")
        print("================================================================")
        print("================================================================")
    except Exception as e:
        print(f"Error writing OTP at word {LIFECYCLE_ADDR}: {e}")
    
def otp_debugkey_write(hpm, args):
    print('Write DEBUG key OTP...')
    print(args.otpfile)
    FT_HARD_LOCK = 0
    ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, FT_HARD_LOCK, 4)
    if ret != 0:
        print("Error reading OTP at HARD_LOCK")
        return
    # 将读取的字节数据转换为整数
    lock_value = int.from_bytes(rs_data, byteorder='little')
    # 打印读取的值
    print(f"OTP at HARD_LOCK: 0x{lock_value:08X}")
    csv_data = read_csv(args.otpfile)
    # 遍历 CSV 文件中的每个 word
    for word_addr, expected_value in csv_data.items():
        time.sleep(0.01)
        if not (12 <= word_addr < 16):
            print(f"Error: Invalid word address {word_addr}")
            continue
        # 计算该 word 属于哪个 4-word 区域
        bit_index = word_addr // 4
        # 判断该 bit 是否为 1（表示该 4-word 区域已锁定）
        if (lock_value >> bit_index) & 1:
            print(f"Warning: Word {word_addr} is in locked region (bit {bit_index} set in HARD_LOCK), skipping write.")
            continue
        # 读取 OTP 数据
        ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, word_addr, 4)  # 每个 word 占 4 字节
        if ret != HPM_OK:
            print(f"Error reading OTP at word {word_addr}")
            continue
        # 将读取的字节数据转换为整数
        actual_value = int.from_bytes(rs_data, byteorder='little')  # 假设是小端字节序
        # 比较值
        if actual_value != expected_value:
            print(f"Mismatch at word {word_addr}: OTP=0x{actual_value:08X}, CSV=0x{expected_value:08X}")
            if actual_value == 0 : 
                print(f"Warning: OTP at word {word_addr} is 0x{actual_value:08X}, which may indicate it is not programmed.")
                # 如果不匹配，尝试写入
                try:
                    hpm.write_memory(hpm.OTP_FLAG, word_addr, 4, expected_value.to_bytes(4, byteorder='little'))
                    print(f"Written OTP at word {word_addr}: 0x{expected_value:08X}")
                except Exception as e:
                    print(f"Error writing OTP at word {word_addr}: {e}")
        else:
            print(f"Word {word_addr} matches: OTP=0x{actual_value:08X}")

    for word_addr, expected_value in csv_data.items():
        time.sleep(0.01)
        if not (12 <= word_addr < 16):
            print(f"Error: Invalid word address {word_addr}")
            continue
        # 计算该 word 属于哪个 4-word 区域
        bit_index = word_addr // 4
        # 判断该 bit 是否为 1（表示该 4-word 区域已锁定）
        if (lock_value >> bit_index) & 1:
            print(f"Warning: Word {word_addr} is in locked region (bit {bit_index} set in HARD_LOCK), skipping write.")
            continue
        # 读取 OTP 中该 word 实际的值（4 字节）
        ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, word_addr, 4)
        if ret != HPM_OK:
            print(f"Error reading OTP at word {word_addr}")
            return
        actual_value = int.from_bytes(rs_data, byteorder='little')
        if actual_value != expected_value:
            print(f"Error: OTP word {word_addr} mismatch!")
            print(f"  Expected: 0x{expected_value:08X}")
            print(f"  Actual  : 0x{actual_value:08X}")
            return  # 一旦发现不一致就中断
    
    if lock_value & 0x00000008:
        print("Warning: DEBUG_KEY region is already locked, skipping lock write.")
        return
    
    lock_value = lock_value | 0x8  # 设置 bit 3 为 1，表示 DEBUG_KEY 区域已锁定
    try:
        hpm.write_memory(hpm.OTP_FLAG, FT_HARD_LOCK, 4, lock_value.to_bytes(4, byteorder='little'))
        print(f"Written HARD_LOCK: 0x{lock_value:08X}")
    except Exception as e:
        print(f"Error writing HARD_LOCK: {e}")
        return

def otp_exipkey_write(hpm, args):
    print('Write EXIP key OTP...')
    print(args.otpfile)
    FT_HARD_LOCK = 0
    ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, FT_HARD_LOCK, 4)
    if ret != 0:
        print("Error reading OTP at HARD_LOCK")
        return
    # 将读取的字节数据转换为整数
    lock_value = int.from_bytes(rs_data, byteorder='little')
    # 打印读取的值
    print(f"OTP at HARD_LOCK: 0x{lock_value:08X}")
    csv_data = read_csv(args.otpfile)
    # 遍历 CSV 文件中的每个 word
    for word_addr, expected_value in csv_data.items():
        time.sleep(0.01)
        if not (96 <= word_addr < 100):
            print(f"Error: Invalid word address {word_addr}")
            continue
        # 计算该 word 属于哪个 4-word 区域
        bit_index = word_addr // 4
        # 判断该 bit 是否为 1（表示该 4-word 区域已锁定）
        if (lock_value >> bit_index) & 1:
            print(f"Warning: Word {word_addr} is in locked region (bit {bit_index} set in HARD_LOCK), skipping write.")
            continue
        # 读取 OTP 数据
        ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, word_addr, 4)  # 每个 word 占 4 字节
        if ret != HPM_OK:
            print(f"Error reading OTP at word {word_addr}")
            continue
        # 将读取的字节数据转换为整数
        actual_value = int.from_bytes(rs_data, byteorder='little')  # 假设是小端字节序
        # 比较值
        if actual_value != expected_value:
            print(f"Mismatch at word {word_addr}: OTP=0x{actual_value:08X}, CSV=0x{expected_value:08X}")
            if actual_value == 0 : 
                print(f"Warning: OTP at word {word_addr} is 0x{actual_value:08X}, which may indicate it is not programmed.")
                # 如果不匹配，尝试写入
                try:
                    hpm.write_memory(hpm.OTP_FLAG, word_addr, 4, expected_value.to_bytes(4, byteorder='little'))
                    print(f"Written OTP at word {word_addr}: 0x{expected_value:08X}")
                except Exception as e:
                    print(f"Error writing OTP at word {word_addr}: {e}")
        else:
            print(f"Word {word_addr} matches: OTP=0x{actual_value:08X}")

    for word_addr, expected_value in csv_data.items():
        time.sleep(0.01)
        if not (96 <= word_addr < 100):
            print(f"Error: Invalid word address {word_addr}")
            continue
        # 计算该 word 属于哪个 4-word 区域
        bit_index = word_addr // 4
        # 判断该 bit 是否为 1（表示该 4-word 区域已锁定）
        if (lock_value >> bit_index) & 1:
            print(f"Warning: Word {word_addr} is in locked region (bit {bit_index} set in HARD_LOCK), skipping write.")
            continue
        # 读取 OTP 中该 word 实际的值（4 字节）
        ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, word_addr, 4)
        if ret != HPM_OK:
            print(f"Error reading OTP at word {word_addr}")
            return
        actual_value = int.from_bytes(rs_data, byteorder='little')
        if actual_value != expected_value:
            print(f"Error: OTP word {word_addr} mismatch!")
            print(f"  Expected: 0x{expected_value:08X}")
            print(f"  Actual  : 0x{actual_value:08X}")
            return  # 一旦发现不一致就中断

    if lock_value & 0x01000000:
        print("Warning: EXIP_KEY region is already locked, skipping lock write.")
        return
    lock_value = lock_value | 0x01000000  # 设置 bit 24 为 1，表示 EXIP_KEY 区域已锁定
    try:
        hpm.write_memory(hpm.OTP_FLAG, FT_HARD_LOCK, 4, lock_value.to_bytes(4, byteorder='little'))
        print(f"Written HARD_LOCK: 0x{lock_value:08X}")
    except Exception as e:
        print(f"Error writing HARD_LOCK: {e}")
        return

def otp_signhash_write(hpm, args):
    print('Write SIGN hash OTP...')
    print(args.otpfile)
    FT_HARD_LOCK = 0
    ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, FT_HARD_LOCK, 4)
    if ret != 0:
        print("Error reading OTP at HARD_LOCK")
        return
    # 将读取的字节数据转换为整数
    lock_value = int.from_bytes(rs_data, byteorder='little')
    # 打印读取的值
    print(f"OTP at HARD_LOCK: 0x{lock_value:08X}")
    csv_data = read_csv(args.otpfile)
    # 遍历 CSV 文件中的每个 word
    for word_addr, expected_value in csv_data.items():
        time.sleep(0.01)
        if not (80 <= word_addr < 88):
            print(f"Error: Invalid word address {word_addr}")
            continue
        # 计算该 word 属于哪个 4-word 区域
        bit_index = word_addr // 4
        # 判断该 bit 是否为 1（表示该 4-word 区域已锁定）
        if (lock_value >> bit_index) & 1:
            print(f"Warning: Word {word_addr} is in locked region (bit {bit_index} set in HARD_LOCK), skipping write.")
            continue
        # 读取 OTP 数据
        ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, word_addr, 4)  # 每个 word 占 4 字节
        if ret != HPM_OK:
            print(f"Error reading OTP at word {word_addr}")
            continue
        # 将读取的字节数据转换为整数
        actual_value = int.from_bytes(rs_data, byteorder='little')  # 假设是小端字节序
        # 比较值
        if actual_value != expected_value:
            print(f"Mismatch at word {word_addr}: OTP=0x{actual_value:08X}, CSV=0x{expected_value:08X}")
            if actual_value == 0 : 
                print(f"Warning: OTP at word {word_addr} is 0x{actual_value:08X}, which may indicate it is not programmed.")
                # 如果不匹配，尝试写入
                try:
                    hpm.write_memory(hpm.OTP_FLAG, word_addr, 4, expected_value.to_bytes(4, byteorder='little'))
                    print(f"Written OTP at word {word_addr}: 0x{expected_value:08X}")
                except Exception as e:
                    print(f"Error writing OTP at word {word_addr}: {e}")
        else:
            print(f"Word {word_addr} matches: OTP=0x{actual_value:08X}")

    for word_addr, expected_value in csv_data.items():
        time.sleep(0.01)
        # 读取 OTP 中该 word 实际的值（4 字节）
        ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, word_addr, 4)
        if ret != HPM_OK:
            print(f"Error reading OTP at word {word_addr}")
            return
        actual_value = int.from_bytes(rs_data, byteorder='little')
        if actual_value != expected_value:
            print(f"Error: OTP word {word_addr} mismatch!")
            print(f"  Expected: 0x{expected_value:08X}")
            print(f"  Actual  : 0x{actual_value:08X}")
            return  # 一旦发现不一致就中断
    print(f"Written SIGN HASH OK")

def otp_factory_write(hpm, args):
    print('factory Write OTP...')
    #读取指定地址的数据
    FT_HARD_LOCK = 0
    FT_LIFE_LOCK = 1
    FT_UID_ADDR = 8
    FT_UID_SIZE = 4   # 4 个 word, 每个 word 占 4 字节
    FT_UUID_ADDR = 88
    FT_UUID_SIZE = 4  # 4 个 word, 每个 word 占 4 字节
    FT_MK_ADDR = 112
    FT_MK_SIZE = 8    # 8 个 word, 每个 word 占 4 字节

    ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, FT_HARD_LOCK, 4)
    if ret != 0:
        print("Error reading OTP at HARD_LOCK")
        return
    lock_value = int.from_bytes(rs_data, byteorder='little')  # 将读取的字节数据转换为整数
    if lock_value != 0:
        print("Warning: HARD_LOCK is not 0x00000000, which may indicate it is already programmed.")
        return
    
    ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, FT_LIFE_LOCK, 4)
    if ret != HPM_OK:
        print("Error reading OTP at FT_LIFE_LOCK")
        return
    life_value = int.from_bytes(rs_data, byteorder='little')  # 将读取的字节数据转换为整数
    if life_value != 0:
        print("Warning: FT_LIFE_LOCK is not 0x00000000, which may indicate it is already programmed.")
        return
    
    for i in range(FT_UID_SIZE):
        ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, FT_UID_ADDR + i, 4) # 读取 OTP 数据
        if ret != 0:
            print(f"Error reading OTP at word {FT_UID_ADDR + i}")
            continue
        actual_value = int.from_bytes(rs_data, byteorder='little')  # 将读取的字节数据转换为整数
        if actual_value == 0: # 打印读取的值
            print(f"Warning: OTP at word {FT_UID_ADDR + i} is not 0x00000000, which may indicate it is already programmed.")
            new_value = random.randint(0, 0xFFFFFFFF)  # 尝试写入新的值, 产生随机数
            try:
                hpm.write_memory(hpm.OTP_FLAG, FT_UID_ADDR + i, 4, new_value.to_bytes(4, byteorder='little'))
                print(f"Written OTP at word {FT_UID_ADDR + i}: 0x{new_value:08X}")
            except Exception as e:
                print(f"Error writing OTP at word {FT_UID_ADDR + i}: {e}")
    
    for i in range(FT_UUID_SIZE):
        ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, FT_UUID_ADDR + i, 4)  # 读取 OTP 数据
        if ret != 0:
            print(f"Error reading OTP at word {FT_UUID_ADDR + i}")
            continue
        actual_value = int.from_bytes(rs_data, byteorder='little')  # 将读取的字节数据转换为整数
        if actual_value == 0: # 打印读取的值
            print(f"Warning: OTP at word {FT_UUID_ADDR + i} is not 0x00000000, which may indicate it is already programmed.")
            new_value = random.randint(0, 0xFFFFFFFF)  # 尝试写入新的值, 产生随机数
            try:
                hpm.write_memory(hpm.OTP_FLAG, FT_UUID_ADDR + i, 4, new_value.to_bytes(4, byteorder='little'))
                print(f"Written OTP at word {FT_UUID_ADDR + i}: 0x{new_value:08X}")
            except Exception as e:
                print(f"Error writing OTP at word {FT_UUID_ADDR + i}: {e}")

    for i in range(FT_MK_SIZE):
        ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, FT_MK_ADDR + i, 4)  # 读取 OTP 数据
        if ret != 0:
            print(f"Error reading OTP at word {FT_MK_ADDR + i}")
            continue
        actual_value = int.from_bytes(rs_data, byteorder='little') # 将读取的字节数据转换为整数
        if actual_value == 0:  # 打印读取的值
            print(f"Warning: OTP at word {FT_MK_ADDR + i} is not 0x00000000, which may indicate it is already programmed.")
            new_value = random.randint(0, 0xFFFFFFFF) # 尝试写入新的值, 产生随机数
            try:
                hpm.write_memory(hpm.OTP_FLAG, FT_MK_ADDR + i, 4, new_value.to_bytes(4, byteorder='little'))
                print(f"Written OTP at word {FT_MK_ADDR + i}: 0x{new_value:08X}")
            except Exception as e:
                print(f"Error writing OTP at word {FT_MK_ADDR + i}: {e}")

    csv_data = read_csv(args.otpfile)
    for word_addr, expected_value in csv_data.items(): # 遍历 CSV 文件中的每个 word
        time.sleep(0.01)
        # 读取 OTP 数据
        ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, word_addr, 4)  # 每个 word_addr 占 4 字节
        if ret != 0:
            print(f"Error reading OTP at word_addr {word_addr}")
            continue
        # 跳过不在合法范围的 OTP 地址（除非是特殊地址 7）
        if word_addr != 7 and word_addr != 64 and (word_addr < 32 or word_addr >= 64):
            print(f"Warning: OTP word_addr {word_addr} is not in the expected range [32, 63], skipping.")
            continue
        # 将读取的字节数据转换为整数
        actual_value = int.from_bytes(rs_data, byteorder='little')  # 假设是小端字节序
        # 比较值
        if actual_value != expected_value:
            print(f"Mismatch at word_addr {word_addr}: OTP=0x{actual_value:08X}, CSV=0x{expected_value:08X}")
            if actual_value == 0 : 
                print(f"Warning: OTP at word_addr {word_addr} is 0x{actual_value:08X}, which may indicate it is not programmed.")
                # 如果不匹配，尝试写入
                try:
                    hpm.write_memory(hpm.OTP_FLAG, word_addr, 4, expected_value.to_bytes(4, byteorder='little'))
                    print(f"Written OTP at word_addr {word_addr}: 0x{expected_value:08X}")
                except Exception as e:
                    print(f"Error writing OTP at word_addr {word_addr}: {e}")
        else:
            print(f"word_addr {word_addr} matches: OTP=0x{actual_value:08X}")
    
    print("====================================")
    lock_value = 0x00000000
    for i in range(FT_MK_SIZE):
        ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, FT_MK_ADDR + i, 4)
        if ret != 0:
            print(f"Error reading OTP at word_addr {FT_MK_ADDR  + i}")
            return
        # 将读取的字节数据转换为整数
        actual_value = int.from_bytes(rs_data, byteorder='little')
        if actual_value == 0:
            return
    lock_value = lock_value | 0x30000000
    
    for i in range(FT_UUID_SIZE):
        ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, FT_UUID_ADDR + i, 4)
        if ret != 0:
            print(f"Error reading OTP at word_addr {FT_UUID_ADDR  + i}")
        # 将读取的字节数据转换为整数
        actual_value = int.from_bytes(rs_data, byteorder='little')
        if actual_value == 0:
            return
    lock_value = lock_value | 0x00400000
 
    for i in range(FT_UID_SIZE):
        ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, FT_UID_ADDR + i, 4)
        if ret != 0:
            print(f"Error reading OTP at word_addr {FT_UID_ADDR + i}")
        # 将读取的字节数据转换为整数
        actual_value = int.from_bytes(rs_data, byteorder='little')
        if actual_value == 0:
            return
    lock_value = lock_value | 0x00000004
    
    csv_data = read_csv(args.otpfile)
    # 遍历 CSV 文件中的每个 word
    for word_addr, expected_value in csv_data.items():
        time.sleep(0.01)
        # 读取 OTP 中该 word 实际的值（4 字节）
        ret, rs_data = hpm.read_memory(hpm.OTP_FLAG, word_addr, 4)
        if ret != HPM_OK:
            print(f"Error reading OTP at word {word_addr}")
            return
        actual_value = int.from_bytes(rs_data, byteorder='little')
        if actual_value != expected_value:
            print(f"Error: OTP word {word_addr} mismatch!")
            print(f"  Expected: 0x{expected_value:08X}")
            print(f"  Actual  : 0x{actual_value:08X}")
            return  # 一旦发现不一致就中断

    lock_value = lock_value | 0x00000002
    lock_value = lock_value | 0x00000010
    # 如果所有 OTP 都已写入，尝试写入锁定值
    print("====================================")
    try:
        time.sleep(0.1)
        hpm.write_memory(hpm.OTP_FLAG, FT_HARD_LOCK, 4, lock_value.to_bytes(4, byteorder='little'))
        print(f"Written OTP at word_addr {FT_HARD_LOCK}: 0x{lock_value:08X}")
        time.sleep(0.1)
        lifecycle_value = 0x10000001
        hpm.write_memory(hpm.OTP_FLAG, FT_LIFE_LOCK, 4, lifecycle_value.to_bytes(4, byteorder='little'))
        print(f"Written OTP at word_addr {FT_LIFE_LOCK}: 0x{lifecycle_value:08X}")
    
    except Exception as e:
        print(f"Error writing OTP at word_addr {word_addr}: {e}")
    
def version(args):
    print(__version__)

def main(argv=None, hpm=None):
    """
    Main function for hpmtool

    argv - Optional override for default arguments parsing (that uses sys.argv), can be a list of custom arguments
    as strings. Arguments and their values need to be added as individual items to the list e.g. "-b 115200" thus
    becomes ['-b', '115200'].

    hpm - Optional override of the connected device previously returned by get_serial_connected_device()
    """

    external_hpm = hpm is not None

    parser = argparse.ArgumentParser(description='hpmtool.py v%s - HPMicro chips ROM ISP Utility' % __version__, prog='hpmtool')

    parser.add_argument('--chip', '-c',
                        help='Target chip type',
                        type=format_chip_name,
                        choices=['auto'] + SUPPORTED_CHIPS,
                        default=os.environ.get('HPMTOOL_CHIP', 'auto'))

    serial_group = parser.add_argument_group('Serial Port Options')

    serial_group.add_argument(
        '--port', '-p',
        help='Serial port device',
        default=os.environ.get('HPMTOOL_PORT', None))

    serial_group.add_argument(
        '--baud', '-b',
        help='Serial port baud rate used when flashing/reading',
        type=arg_auto_int,
        default=os.environ.get('HPMTOOL_BAUD', HPMLoader.DEFAULT_BAUD))

    serial_group.add_argument(
        '--before',
        help='What to do before connecting to the chip',
        choices=['default_reset', 'hot_reset', 'cold_reset'],
        default=os.environ.get('HPMTOOL_BEFORE', 'default_reset'))

    serial_group.add_argument(
        '--after', '-a',
        help='What to do after hpmtool.py is finished',
        choices=['default_reset', 'hot_reset', 'cold_reset'],
        default=os.environ.get('HPMTOOL_AFTER', 'default_reset'))

    usb_group = parser.add_argument_group('USB HID Options')

    # 添加一个没有参数的选项
    usb_group.add_argument(
        '--USB', '-u',
        help='USB hid device',
        action='store_true'
    )

    parser.add_argument(
        '--sign_boot',
        help="Disable launching the flasher stub, only talk to ROM bootloader. Some features will not be available.",
        action='store_true')

    parser.add_argument(
        '--trace', '-t',
        help="Enable trace-level output of hpmtool.py interactions.",
        action='store_true')

    parser.add_argument(
        '--connect-attempts',
        help=('Number of attempts to connect, negative or 0 for infinite. '
              'Default: %d.' % DEFAULT_CONNECT_ATTEMPTS),
        type=int,
        default=os.environ.get('HPMTOOL_CONNECT_ATTEMPTS', DEFAULT_CONNECT_ATTEMPTS))

    subparsers = parser.add_subparsers(
        dest='operation',
        help='Run hpmtool {command} -h for additional help')

    subparsers.add_parser('chip_id', help='Read Chip ID from OTP ROM')

    subparsers.add_parser('eth_mac',help='Read MAC address from OTP ROM')

    parser_load_image = subparsers.add_parser(
        'load_image',
        help='Download an image to RAM and execute')
    parser_load_image.add_argument('filename', help='Firmware image')

    parser_write_mem = subparsers.add_parser(
        'write_mem',
        help='Read-modify-write to arbitrary memory location')
    parser_write_mem.add_argument('address', help='Address to write', type=arg_auto_int)
    parser_write_mem.add_argument('value', help='Value', type=arg_auto_int)
    parser_write_mem.add_argument('mask', help='Mask of bits to write', type=arg_auto_int, nargs='?', default='0xFFFFFFFF')

    parser_read_mem = subparsers.add_parser(
        'read_mem',
        help='Read arbitrary memory location')
    parser_read_mem.add_argument('address', help='Address to read', type=arg_auto_int)

    parser_dump_mem = subparsers.add_parser(
        'dump_mem',
        help='Dump arbitrary memory to disk')
    parser_dump_mem.add_argument('address', help='Base address', type=arg_auto_int)
    parser_dump_mem.add_argument('size', help='Size of region to dump', type=arg_auto_int)
    parser_dump_mem.add_argument('filename', help='Name of binary dump')

    def add_spi_flash_subparsers(args, parent, bin_addr_filename=None, auto_detect = False):
        detect_status       = False
        detect_flash_type   = "SFDP_SDR"
        detect_pad_power_on = "SPI"
        detect_pad_config   = "SPI"
        detect_flash_qes    = "not_needed"
        detect_flash_misc   = "not_used"
        detect_flash_freq   = "120m"
        detect_flash_voltage = "3.3V"
        detect_flash_group   = "1st_group"
        detect_flash_selection = "CA_CS0"
        detect_flash_strength = 0
        detect_flash_option = "4MB"

        if auto_detect:
            if bin_addr_filename is not None:
                addr, file_obj = bin_addr_filename[0]
                # 读取文件的前 16 字节并转换为 16 进制格式
                file_obj.seek(0)  # 确保从文件开头读取
                first_16_bytes = file_obj.read(16)
                if len(first_16_bytes) >= 4 and first_16_bytes[3] == 0xFC and first_16_bytes[2] == 0xF9:
                    flash_config = struct.unpack('<4I', first_16_bytes)
                else:
                    file_obj.seek(0x400)  # 确保从文件0x400读取
                    first_16_bytes = file_obj.read(16)
                    if len(first_16_bytes) >= 4 and first_16_bytes[3] == 0xFC and first_16_bytes[2] == 0xF9:
                        flash_config = struct.unpack('<4I', first_16_bytes)
                    else:
                        chip_class = _chip_to_rom_loader(args.chip)
                        flash_config = chip_class.OPTION
                        
                if (flash_config[0] >> 16) & 0xFFFF == HPM_IMAGE_MAGIC:
                    print("Flash configuration found in image header")
                    detect_flash_type = get_item_key(FLASH_PROBE_TYPE, (flash_config[1] >> 28) & 0xF)
                    detect_pad_power_on = get_item_key(FLASH_PAD_AFTER, (flash_config[1] >> 24) & 0xF)
                    detect_pad_config = get_item_key(FLASH_PAD_AFTER, (flash_config[1] >> 20) & 0xF)
                    detect_flash_qes = get_item_key(FLASH_QES, (flash_config[1] >> 16) & 0xF)
                    detect_flash_misc = get_item_key(FLASH_MISC, (flash_config[1] >> 4) & 0xF)
                    detect_flash_freq = get_item_key(FLASH_FREQ, flash_config[1] & 0xF)
                    detect_flash_voltage = get_item_key(FLASH_VOLTAGE, (flash_config[2] >> 16) & 0xF)
                    detect_flash_group = get_item_key(FLASH_GROUP, (flash_config[2] >> 12) & 0xF)
                    detect_flash_selection = get_item_key(FLASH_SELECTION, (flash_config[2] >> 8) & 0xF)
                    detect_flash_strength = flash_config[2] & 0xF
                    detect_flash_option = get_item_key(FLASH_SIZE, flash_config[3]& 0xFF)
                    print("Auto-detecting flash configuration...")
                    detect_status = False
        #    [31:28] Flash probe type
        #      0 - SFDP SDR / 1 - SFDP DDR
        #      2 - 1-4-4 Read (0xEB, 24-bit address) / 3 - 1-2-2 Read(0xBB, 24-bit address)
        #      4 - HyperFLASH 1.8V / 5 - HyperFLASH 3V
        #      6 - OctaBus DDR (SPI -> OPI DDR)
        #      8 - Xccela DDR (SPI -> OPI DDR)
        #      10 - EcoXiP DDR (SPI -> OPI DDR)
        parent.add_argument('--flash_type', '-ft', help='SPI Flash probe type',
                            choices=['SFDP_SDR', 'SFDP_DDR', '1-4-4_Read', '1-2-2_Read', 'HyperFLASH_1.8V',\
                                                        'HyperFLASH_3V', 'OctaBus_DDR', 'Xccela_DDR', "EcoXiP_DDR"],
                            required=detect_status,default=detect_flash_type)
        #    [27:24] Command Pads after Power-on Reset
        #      0 - SPI / 1 - DPI / 2 - QPI / 3 - OPI
        parent.add_argument('--pad_power_on', '-ppo', help='Command Pads after Power-on Reset',
                            choices=['SPI', 'DPI', 'QPI', 'OPI'],
                            required=detect_status, default=detect_pad_power_on)
        #    [23:20] Command Pads after Configuring FLASH
        #      0 - SPI / 1 - DPI / 2 - QPI / 3 - OPI
        parent.add_argument('--pad_config', '-pac', help='Command Pads after Configuring FLASH',
                            choices=['SPI', 'DPI', 'QPI', 'OPI'],
                            required=detect_status, default=detect_pad_config)
        # [19:16] Quad Enable Sequence (for the device support SFDP 1.0 only)
        #  Quad Enable Sequence
        #     0 - Not needed
        #      1 - QE bit is at bit 6 in Status Register 1
        #      2 - QE bit is at bit1 in Status Register 2
        #      3 - QE bit is at bit7 in Status Register 2
        #      4 - QE bit is at bit1 in Status Register 2 and should be programmed by 0x31
        parent.add_argument('--flash_qes', '-fqe', help='SPI Flash Quad Enable Sequences',
                            choices=['not_needed', 'reg1bit6', 'reg2bit1', 'reg2bit7','reg2bit1_0x31'],
                            required=detect_status, default=detect_flash_qes)
        
        # [7:4] Misc.
        # 0 - Not used,  1 - SPI mode, 2 - Internal loopback, 3 - External DQS
        parent.add_argument('--flash_misc', '-fm', help='SPI Flash Misc settings',
                            choices=['not_used', 'spi_mode', 'loopback', 'qds'],
                            required=detect_status, default=detect_flash_misc)
        #  [3:0] Frequency option
        #   1 - 30MHz / 2 - 50MHz / 3 - 66MHz / 4 - 80MHz / 5 - 100MHz / 6 - 120MHz / 7 - 133MHz / 8 - 166MHz
        parent.add_argument('--flash_freq', '-ff', help='SPI Flash frequency',
                            choices=['166m', '133m', '120m', '100m', '80m', '66m', '50m', '30m'],
                            required=detect_status, default=detect_flash_freq)
        #    [19:16] IO voltage
        #      0 - 3V / 1 - 1.8V
        parent.add_argument('--flash_voltage', '-fv', help='SPI Flash IO voltage',
                            choices=['3.3V', '1.8V'],
                            required=detect_status, default=detect_flash_voltage)
        #    [15:12] Pin group
        #      0 - 1st group / 1 - 2nd group
        parent.add_argument('--flash_group', '-fg', help='SPI Flash Pin group',
                            choices=['1st_group', '2nd_group'],
                            required=detect_status, default=detect_flash_group)
        #    [11:8] Connection selection
        #      0 - CA_CS0 / 1 - CB_CS0 / 2 - CA_CS0 + CB_CS0 (Two FLASH connected to CA and CB respectively)
        parent.add_argument('--flash_selection', '-fcs', help='SPI Flash Connection selection',
                            choices=['CA_CS0', 'CB_CS0', 'CA_CS0+CB_CS0'],
                            required=detect_status, default=detect_flash_selection)
        #    [7:0] Drive Strength
        #      0 - Default value
        parent.add_argument('--flash_strength', '-fds', help='SPI Flash Drive Strength',type=arg_auto_int,
                            required=detect_status, default=detect_flash_strength)
        #    [7:0] Flash Size Option
        #      0 - 4MB / 1 - 8MB / 2 - 16MB
        parent.add_argument('--flash_option', '-fo', help='SPI Flash Size Option',
                            choices=['4MB', '8MB', '16MB'],
                            required=detect_status, default=detect_flash_option)

    parser_image_info = subparsers.add_parser('image_info', help='Dump headers from an application image')
    parser_image_info.add_argument('filename', help='Image file to parse')

    parser_write_flash = subparsers.add_parser(
        'write_flash', help='Write a binary blob to flash')

    parser_write_flash.add_argument('addr_filename', metavar='<address> <filename>', help='Address followed by binary filename, separated by space',
                                    action=AddrFilenamePairAction)

    parser_write_flash.add_argument('--verify', help='Verify just-written data on flash '
                                    '(mostly superfluous, data is read back during flashing)', action='store_true')
    parser_write_flash.add_argument('--encrypt', help='Apply flash encryption when writing data (required correct efuse settings)',
                                    action='store_true')
    # In order to not break backward compatibility, our list of encrypted files to flash is a new parameter
    parser_write_flash.add_argument('--encrypt-files', metavar='<address> <filename>',
                                    help='Files to be encrypted on the flash. Address followed by binary filename, separated by space.',
                                    action=AddrFilenamePairAction)
    parser_write_flash.add_argument('--ignore-flash-encryption-efuse-setting', help='Ignore flash encryption efuse settings ',
                                    action='store_true')

    parser_read_flash = subparsers.add_parser(
        'read_flash',help='Read SPI flash content')
    parser_read_flash.add_argument('address', help='Start address', type=arg_auto_int)
    parser_read_flash.add_argument('size', help='Size of region to dump', type=arg_auto_int)
    parser_read_flash.add_argument('filename', help='Name of binary dump')

    parser_verify_flash = subparsers.add_parser(
        'verify_flash', help='Verify a binary blob against flash')
    parser_verify_flash.add_argument('addr_filename', help='Address and binary file to verify there, separated by space',
                                     action=AddrFilenamePairAction)
    parser_verify_flash.add_argument('--diff', '-d', help='Show differences',
                                     choices=['no', 'yes'], default='no')

    parser_erase_flash = subparsers.add_parser('erase_flash',help='Perform Chip Erase on SPI flash')
    parser_erase_region = subparsers.add_parser('erase_region', help='Erase a region of the flash')
    parser_erase_region.add_argument('address', help='Start address (must be multiple of 4096)', type=arg_auto_int)
    parser_erase_region.add_argument('size', help='Size of region to erase (must be multiple of 4096)', type=arg_auto_int)

    subparsers.add_parser('otp_dump',help='Read CHIP OTP content')
    subparsers.add_parser('otp_summary',help='Summary CHIP OTP content')

    otp_write_parser = subparsers.add_parser('otp_write',help='write CHIP OTP content')
    otp_write_parser.add_argument('-of', '--otpfile', type=str, required=True, help='Path to the CSV file containing OTP data')

    otp_factory_write_parser = subparsers.add_parser('otp_factory_write',help='Factory write CHIP OTP content')
    otp_factory_write_parser.add_argument('-of', '--otpfile', type=str, required=True, help='Path to the CSV file containing OTP data')
   
    otp_debugkey_write_parser = subparsers.add_parser('otp_debugkey_write',help='Debug Key write CHIP OTP content')
    otp_debugkey_write_parser.add_argument('-of', '--otpfile', type=str, required=True, help='Path to the CSV file containing OTP data')
    
    otp_exipkey_write_parser = subparsers.add_parser('otp_exipkey_write',help='Exip Key write CHIP OTP content')
    otp_exipkey_write_parser.add_argument('-of', '--otpfile', type=str, required=True, help='Path to the CSV file containing OTP data')
    
    otp_signhash_write_parser = subparsers.add_parser('otp_signhash_write',help='Sign Hash write CHIP OTP content')
    otp_signhash_write_parser.add_argument('-of', '--otpfile', type=str, required=True, help='Path to the CSV file containing OTP data')

    subparsers.add_parser('otp_exip_enable',help='otp exip enable CHIP OTP content')
    subparsers.add_parser('otp_lifecycle_enable',help='otp life cycle enable CHIP OTP content')
    subparsers.add_parser('rom_reset',help='reset chip')
    
    subparsers.add_parser('version', help='Print hpmtool version')

    bin_addr_filename = None
    args, unknown  = parser.parse_known_args()
    if hasattr(args, 'addr_filename'):
        bin_addr_filename = args.addr_filename
    
    add_spi_flash_subparsers(args, parser_write_flash,  bin_addr_filename, auto_detect=True)
    add_spi_flash_subparsers(args, parser_read_flash,   auto_detect=False)
    add_spi_flash_subparsers(args, parser_verify_flash, bin_addr_filename, auto_detect=True)
    add_spi_flash_subparsers(args, parser_erase_flash,  auto_detect=False)
    add_spi_flash_subparsers(args, parser_erase_region, auto_detect=False)

    # internal sanity check - every operation matches a module function of the same name
    for operation in subparsers.choices.keys():
        assert operation in globals(), "%s should be a module function" % operation
    
    argv = expand_file_arguments(argv or sys.argv[1:])
    
    args = parser.parse_args(argv)
    print('hpmtool.py v%s' % __version__)

    # operation function can take 1 arg (args), 2 args (hpm, arg)
    # or be a member function of the HPMLoader class.
    if args.operation is None:
        parser.print_help()
        sys.exit(1)

    # Forbid the usage of both --encrypt, which means encrypt all the given files,
    # and --encrypt-files, which represents the list of files to encrypt.
    # The reason is that allowing both at the same time increases the chances of
    # having contradictory lists (e.g. one file not available in one of list).
    if args.operation == "write_flash" and args.encrypt and args.encrypt_files is not None:
        raise FatalError("Options --encrypt and --encrypt-files must not be specified at the same time.")

    operation_func = globals()[args.operation]

    if PYTHON2:
        # This function is depreciated in Python3
        operation_args = inspect.getargspec(operation_func).args
    else:
        operation_args = inspect.getfullargspec(operation_func).args

    if operation_args[0] == 'hpm':  # operation function takes an HPMLoader connection object
        if args.USB:
            usb_list = hid.enumerate()
            hpm = hpm or get_usb_connected_device(usb_list, connect_attempts=args.connect_attempts, chip=args.chip, trace=args.trace)
            if hpm is None:
                raise FatalError("Could not connect to an HPMciro device on any of the %d available usb ports." % len(usb_list))
    
        else :
            if args.before != "no_reset_no_sync":
                initial_baud = min(HPMLoader.DEFAULT_BAUD, args.baud)  # don't sync faster than the default baud rate
            else:
                initial_baud = args.baud

            if args.port is None:
                ser_list = get_port_list()
                print("Found %d serial ports" % len(ser_list))
            else:
                ser_list = get_port_list()
                if args.port in ser_list:
                    ser_list = [args.port]
                else:
                    raise FatalError("Port %s not found" % args.port)

            hpm = hpm or get_serial_connected_device(ser_list, connect_attempts=args.connect_attempts, port=args.port,
                                                    initial_baud=initial_baud, chip=args.chip, trace=args.trace, before=args.before)
            if hpm is None:
                raise FatalError("Could not connect to an HPMciro device on any of the %d available serial ports." % len(ser_list))

            if args.baud > initial_baud:
                try:
                    hpm.change_baud(args.baud)
                except NotImplementedInROMError:
                    print("WARNING: ROM doesn't support changing baud rate. Keeping initial baud rate %d" % initial_baud)
        
        rom_ver, life = hpm.query_rte_rom()
        if life == 0x08 and args.sign_boot and rom_ver > 0x56010000:
            print("RTE ROM is not life enabled, running in no_stub mode")
            bootfile_name = f"./bl_fw/{hpm.CHIP_NAME}/{args.chip}_blfw_signed.bin"
            load_image(hpm, bootfile_name)
            time.sleep(1)  # wait for the USB device to settle
            if args.USB:
                time.sleep(0.1)
                print("USB HID mode is not supported in no_stub mode")
                usb_list = hid.enumerate()
                hpm = get_usb_connected_device(usb_list, connect_attempts=args.connect_attempts, chip=args.chip, trace=args.trace)
                if hpm is None:
                    raise FatalError("Could not connect to an HPMciro device on any of the %d available usb ports." % len(usb_list))
                print("Connected to HPM device:", hpm)

        try:
            operation_func(hpm, args)
        finally:
            try:  # Clean up AddrFilenamePairAction files
                for address, argfile in args.addr_filename:
                    argfile.close()
            except AttributeError:
                pass

        # Handle post-operation behaviour (reset or other)
        if operation_func == load_image:
            # the hpm is now running the loaded image, so let it run
            print('Exiting immediately.')
        elif args.after == 'hard_reset':
            hpm.hard_reset()
        elif args.after == 'soft_reset':
            print('Soft resetting...')
            # flash_finish will trigger a soft reset
            hpm.soft_reset(False)
        elif args.after == 'no_reset_stub':
            print('Staying in flasher stub.')
        else:  # args.after == 'no_reset'
            print('Staying in bootloader.')
            if hpm.IS_STUB:
                hpm.soft_reset(True)  # exit stub back to ROM loader

        if not external_hpm:
            hpm.close()

    else:
        operation_func(args)

def get_port_list():
    if list_ports is None:
        raise FatalError("Listing all serial ports is currently not available. Please try to specify the port when "
                         "running hpmtool.py or update the pyserial package to the latest version")
    return sorted(ports.device for ports in list_ports.comports())

def expand_file_arguments(argv):
    """ Any argument starting with "@" gets replaced with all values read from a text file.
    Text file arguments can be split by newline or by space.
    Values are added "as-is", as if they were specified in this order on the command line.
    """
    new_args = []
    expanded = False
    for arg in argv:
        if arg.startswith("@"):
            expanded = True
            with open(arg[1:], "r") as f:
                for line in f.readlines():
                    new_args += shlex.split(line)
        else:
            new_args.append(arg)
    if expanded:
        print("hpmtool.py %s" % (" ".join(new_args[1:])))
        return new_args
    return argv

class AddrFilenamePairAction(argparse.Action):
    """ Custom parser class for the address/filename pairs passed as arguments """
    def __init__(self, option_strings, dest, nargs='+', **kwargs):
        super(AddrFilenamePairAction, self).__init__(option_strings, dest, nargs, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        # validate pair arguments
        pairs = []
        for i in range(0, len(values), 2):
            try:
                address = int(values[i], 0)
            except ValueError:
                raise argparse.ArgumentError(self, 'Address "%s" must be a number' % values[i])
            try:
                argfile = open(values[i + 1], 'rb')
            except IOError as e:
                raise argparse.ArgumentError(self, e)
            except IndexError:
                raise argparse.ArgumentError(self, 'Must be pairs of an address and the binary filename to write there')
            pairs.append((address, argfile))

        # Sort the addresses and check for overlapping
        end = 0
        for address, argfile in sorted(pairs, key=lambda x: x[0]):
            argfile.seek(0, 2)  # seek to end
            size = argfile.tell()
            argfile.seek(0)
            sector_start = address & ~(HPMLoader.FLASH_SECTOR_SIZE - 1)
            sector_end = ((address + size + HPMLoader.FLASH_SECTOR_SIZE - 1) & ~(HPMLoader.FLASH_SECTOR_SIZE - 1)) - 1
            if sector_start < end:
                message = 'Detected overlap at address: 0x%x for file: %s' % (address, argfile.name)
                raise argparse.ArgumentError(self, message)
            end = sector_end
        setattr(namespace, self.dest, pairs)

def _main():
    try:
        main()
    except FatalError as e:
        print('\nA fatal error occurred: %s' % e)
        sys.exit(2)

if __name__ == '__main__':
    _main()
