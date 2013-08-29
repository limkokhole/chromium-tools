# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Handles generating profiles and transferring them to/from mobile devices."""

import logging
import os
import shutil
import sys
import tempfile

from telemetry.core import browser_options
from telemetry.core import discover
from telemetry.core import util
from telemetry.page import page_runner
from telemetry.page import profile_creator
from telemetry.page import test_expectations


def _DiscoverProfileCreatorClasses():
  profile_creators_dir = os.path.abspath(os.path.join(util.GetBaseDir(),
      os.pardir, 'perf', 'profile_creators'))
  base_dir = os.path.abspath(os.path.join(profile_creators_dir, os.pardir))

  profile_creators_unfiltered = discover.DiscoverClasses(
      profile_creators_dir, base_dir, profile_creator.ProfileCreator)

  # Remove '_creator' suffix from keys.
  profile_creators = {}
  for test_name, test_class in profile_creators_unfiltered.iteritems():
    assert test_name.endswith('_creator')
    test_name = test_name[:-len('_creator')]
    profile_creators[test_name] = test_class
  return profile_creators

def GenerateProfiles(profile_creator_class, profile_creator_name, options):
  """Generate a profile"""
  expectations = test_expectations.TestExpectations()
  test = profile_creator_class()

  temp_output_directory = tempfile.mkdtemp()
  options.output_profile_path = temp_output_directory

  results = page_runner.Run(test, test.page_set, expectations, options)

  if results.errors or results.failures:
    logging.warning('Some pages failed.')
    if results.errors or results.failures:
      logging.warning('Failed pages:\n%s',
                      '\n'.join(zip(*results.errors + results.failures)[0]))
    return 1

  # Everything is a-ok, move results to final destination.
  generated_profiles_dir = os.path.abspath(os.path.join(util.GetBaseDir(),
      os.pardir, os.pardir, 'out', 'Release', 'generated_profiles'))
  if not os.path.exists(generated_profiles_dir):
    os.makedirs(generated_profiles_dir)
  out_path = os.path.join(generated_profiles_dir, profile_creator_name)
  shutil.move(temp_output_directory, out_path)
  sys.stderr.write("SUCCESS: Generated profile copied to: '%s'.\n" % out_path)

  return 0

def Main():
  profile_creators = _DiscoverProfileCreatorClasses()

  options = browser_options.BrowserFinderOptions()
  parser = options.CreateParser("%%prog <profile_type> <--browser=...>")
  page_runner.AddCommandLineOptions(parser)
  _, args = parser.parse_args()

  # Sanity check arguments.
  legal_profile_creators = '|'.join(profile_creators.keys())
  if len(args) != 1:
    raise Exception("No profile type argument specified legal values are: %s" %
        legal_profile_creators)

  if args[0] not in profile_creators.keys():
    raise Exception("Invalid profile type, legal values are: %s" %
        legal_profile_creators)

  if not options.browser_type:
    raise Exception("Must specify --browser option.")

  if options.dont_override_profile:
    raise Exception("Can't use existing profile when generating profile.")

  # Generate profile.
  profile_creator_class = profile_creators[args[0]]
  return GenerateProfiles(profile_creator_class, args[0], options)
