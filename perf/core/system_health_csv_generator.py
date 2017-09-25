# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import csv
import page_sets


def IterAllSystemHealthStories():
  for s in page_sets.SystemHealthStorySet(platform='desktop'):
    yield s
  for s in page_sets.SystemHealthStorySet(platform='mobile'):
    if len(s.SUPPORTED_PLATFORMS) < 2:
      yield s


def GenerateSystemHealthCSV(file_path):
  system_health_stories = list(IterAllSystemHealthStories())
  system_health_stories.sort(key=lambda s: s.name)
  with open(file_path, 'w') as f:
    csv_writer = csv.writer(f)
    csv_writer.writerow([
        'Story name', 'Platform', 'Description'])
    for s in system_health_stories:
      p = s.SUPPORTED_PLATFORMS
      if len(p) == 2:
        p = 'all'
      else:
        p = list(p)[0]
      csv_writer.writerow([s.name, p, s.GetStoryDescription()])
  return 0
