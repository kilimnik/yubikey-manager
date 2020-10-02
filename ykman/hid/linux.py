from __future__ import absolute_import

from yubikit.core.otp import OtpConnection
from .base import OtpYubiKeyDevice, YUBICO_VID, USAGE_OTP

import glob
import fcntl
import struct
import logging

logger = logging.getLogger(__name__)


GET_REPORT = 0xC0094807
SET_REPORT = 0xC0094806
GET_INFO = 0x80084803
GET_DESC_SIZE = 0x80044801
GET_DESC = 0x90044802


class HidrawConnection(OtpConnection):
    def __init__(self, path):
        self.handle = open(path, "wb")

    def close(self):
        self.handle.close()

    def receive(self):
        buf = bytearray(1 + 8)
        fcntl.ioctl(self.handle, GET_REPORT, buf, True)
        return buf[1:]

    def send(self, data):
        buf = bytearray([9])
        buf.extend(data)
        fcntl.ioctl(self.handle, SET_REPORT, buf, True)


def get_info(dev):
    buf = bytearray(4 + 2 + 2)
    fcntl.ioctl(dev, GET_INFO, buf, True)
    return struct.unpack("<IHH", buf)


def get_descriptor(dev):
    buf = bytearray(4)
    fcntl.ioctl(dev, GET_DESC_SIZE, buf, True)
    size = struct.unpack("<I", buf)[0]
    buf += bytearray(size)
    fcntl.ioctl(dev, GET_DESC, buf, True)
    return buf[4:]


def get_usage(dev):
    buf = get_descriptor(dev)
    usage, usage_page = (None, None)
    while buf:
        head, buf = buf[0], buf[1:]
        typ, size = 0xFC & head, 0x03 & head
        value, buf = buf[:size], buf[size:]
        if typ == 4:  # Usage page
            usage_page = struct.unpack("<I", value.ljust(4, b"\0"))[0]
            if usage is not None:
                return usage_page, usage
        elif typ == 8:  # Usage
            usage = struct.unpack("<I", value.ljust(4, b"\0"))[0]
            if usage_page is not None:
                return usage_page, usage


def list_devices():
    devices = []
    for hidraw in glob.glob("/dev/hidraw*"):
        usage = None
        try:
            with open(hidraw, "rb") as f:
                bustype, vid, pid = get_info(f)
                if vid == YUBICO_VID:
                    usage = get_usage(f)
        except Exception as e:
            logger.debug("Failed opening HID device", exc_info=e)
            continue

        if usage == USAGE_OTP:
            devices.append(OtpYubiKeyDevice(hidraw, pid, HidrawConnection))

    return devices