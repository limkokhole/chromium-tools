# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import json
import urlparse
import os

class Page(object):
  def __init__(self, url, attributes=None):
    self.url = url
    parsed_url = urlparse.urlparse(url)
    if parsed_url.scheme == None: # pylint: disable=E1101
      raise Exception('urls must be fully qualified: %s' % url)
    self.interactions = 'scroll'
    self.credentials = None
    self.is_gmail = False
    self.wait_time_after_navigate = 2
    self.scroll_is_infinite = False

    if attributes:
      for k, v in attributes.iteritems():
        setattr(self, k, v)

  def __str__(self):
    return self.url

class PageSet(object):
  def __init__(self, base_dir='', attributes=None):
    self.description = ''
    self.archive_path = ''
    self.base_dir = base_dir
    self.credentials_path = None

    if attributes:
      for k, v in attributes.iteritems():
        setattr(self, k, v)

    if self.credentials_path is not None:
      self.credentials_path = os.path.join(self.base_dir,
                                           self.credentials_path)

    self.pages = []

  @classmethod
  def FromFile(cls, file_path):
    with open(file_path, 'r') as f:
      contents = f.read()
      data = json.loads(contents)
      return cls.FromDict(data, os.path.dirname(file_path))

  @classmethod
  def FromDict(cls, data, file_path=''):
    page_set = cls(file_path, data)
    for page_attributes in data['pages']:
      url = page_attributes.pop('url')
      page = Page(url, page_attributes)
      page_set.pages.append(page)
    return page_set

  def __iter__(self):
    return self.pages.__iter__()
