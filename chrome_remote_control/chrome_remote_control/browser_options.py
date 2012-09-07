# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import optparse
import sys
import shlex

import browser_finder

class BrowserOptions(optparse.Values):
  """Options to be used for discovering and launching a browser."""

  def __init__(self, type = None):
    optparse.Values.__init__(self)
    self.dont_override_profile = False
    self.show_stdout = False
    self.browser_executable = None
    self.browser_type =  type
    self.chrome_root = None
    self.android_device = None
    self.extra_browser_args = []

  def CreateParser(self, *args, **kwargs):
    parser = optparse.OptionParser(*args, **kwargs)

    # Selection group
    group = optparse.OptionGroup(parser, "Which browser to use")
    group.add_option('--browser',
        dest='browser_type',
        default=None,
        help='Browser type to run, '
             'in order of priority. Supported values: list,%s' %
             browser_finder.ALL_BROWSER_TYPES)
    group.add_option('--browser-executable',
        dest='browser_executable',
        help='The exact browser to run.')
    group.add_option('--chrome-root',
        dest='chrome_root',
        help='Where to look for chrome builds.'
             'Defaults to searching parent dirs by default.')
    group.add_option('--device',
        dest='android_device',
        help='The android device ID to use'
             'If not specified, only 0 or 1 connected devcies are supported.')
    parser.add_option_group(group)

    # Browser options
    group = optparse.OptionGroup(parser, "Browser options")
    group.add_option('--dont-override-profile', action='store_true',
        dest='dont_override_profile',
        help='Uses the regular user profile instead of a clean one')
    group.add_option('--extra-browser-args',
        dest='extra_browser_args_as_string',
        help='Additional arguments to pass to the browser when it starts')
    group.add_option('--show-stdout',
        action='store_true',
        help="When possible, will display the stdout of the process")
    parser.add_option_group(group)

    real_parse = parser.parse_args
    def ParseArgs(args=None):
      defaults = parser.get_default_values()
      for k, v in defaults.__dict__.items():
        if k in self.__dict__:
          continue
        self.__dict__[k] = v
      ret = real_parse(args, self)
      if self.browser_executable and not self.browser_type:
        self.browser_type = 'exact'
      if not self.browser_executable and not self.browser_type:
        sys.stderr.write("Must provide --browser=<type>\n")
        sys.exit(1)
      if self.browser_type == 'list':
        import browser_finder
        types = browser_finder.GetAllAvailableBrowserTypes(self)
        sys.stderr.write("Available browsers:\n");
        sys.stdout.write("  %s\n" % "\n  ".join(types))
        sys.exit(1)
      if self.extra_browser_args_as_string:
        tmp = shlex.split(self.extra_browser_args_as_string)
        self.extra_browser_args.extend(tmp)
        delattr(self, 'extra_browser_args_as_string')
      return ret
    parser.parse_args = ParseArgs
    return parser

"""
This global variable can be set to a BrowserOptions object by the test harness
to allow multiple unit tests to use a specific browser, in face of multiple
options.
"""
options_for_unittests = None
