# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Package entry-point."""

import argparse

import netifaces
from tornado import ioloop

import linux_gadgetfs
import server


def ParseArgs():
  """Parse application arguments."""
  parser = argparse.ArgumentParser(description='USB gadget server.')
  parser.add_argument(
      '-i', '--interface', default='lo',
      help='Listen for HTTP connections on this interface.')
  parser.add_argument(
      '-p', '--port', default=8080,
      help='Listen for HTTP connections on this port.')
  parser.add_argument(
      '--hardware', default='beaglebone-black',
      help='Hardware configuration.')
  return parser.parse_args()


def main():
  args = ParseArgs()

  server.interface = args.interface
  server.port = args.port
  server.hardware = args.hardware

  addrs = netifaces.ifaddresses(server.interface)
  ip_address = addrs[netifaces.AF_INET][0]['addr']
  server.address = '{}:{}'.format(ip_address, server.port)

  server.chip = linux_gadgetfs.LinuxGadgetfs(server.hardware)
  server.SwitchGadget(server.default)

  server.http_server.listen(server.port)

  ioloop.IOLoop.instance().start()
  print 'Exiting...'


if __name__ == '__main__':
  main()
