/* Copyright (c) 2012 The Chromium Authors. All rights reserved.
 * Use of this source code is governed by a BSD-style license that can be
 * found in the LICENSE file.
 */
'use strict';

base.require('model.layer_tree_host_impl');

base.exportTo('ccfv', function() {

  function unquoteIfNeeded(val) {
    if (val[0] == '{' && val[val.length - 1] == '}') {
      return JSON.parse(val);
    } else {
      return val;
    }
  }

  /**
   * A generic container for data from a cc trace.
   *
   * @constructor
   */
  function Model() {
    this.lthiHistories = {};
  }
  Model.prototype = {
    getOrCreateLTHIHistory: function(id) {
      if (this.lthiHistories[id] === undefined)
        this.lthiHistories[id] = new ccfv.model.LayerTreeHostImplHistory(id);
      return this.lthiHistories[id];
    },

    initFromTraceEvents: function(trace) {
      var importer = new TraceImporter();
      importer.importTraceIntoModel(this, trace);
    }
  };

  function TraceImporter() {
  };

  TraceImporter.prototype = {
    addWarning: function(msg) {},

    importTraceIntoModel: function(model, trace) {
      this.model = model;

      var events = trace.traceEvents;
      for (var i = 0; i < events.length; i++) {
        var event = events[i];
        if (event.name == 'Frame')
          this.handleFrameEvent(event);
      }
    },

    handleFrameEvent: function(event) {
      if (typeof event.args.frame !== 'string')
        throw new Error('Expected Frame to have args.frame of type string.');
      var frameData = unquoteIfNeeded(event.args.frame);

      var lthiID;
      if (frameData.lthi_id === undefined) {
        // Old versions used compositor_instance instead of lthiID.
        if (frameData.compositor_instance === undefined)
          throw new Error('Expected Frame to have a lthi_id field.');
        lthiID = frameData.compositor_instance;
      } else {
        lthiID = frameData.lthi_id;
      }

      var lthiHistory = this.model.getOrCreateLTHIHistory(lthiID);

      var lthi = lthiHistory.createNewLTHI();

      // Basic properties.
      if (frameData.device_viewport_size === undefined)
        throw new Error('Expected device_viewport');
      lthi.deviceViewportSize = frameData.device_viewport_size;

      // Tiles.
      if (frameData.tiles === undefined)
        throw new Error('Expected tiles');
      frameData.tiles.forEach(function(tile) {
        this.handleFrameTile(lthi, tile);
      }, this);
    },

    handleFrameTile: function(lthi, tileData) {
      var tileID;
      if (!tileData.id) {
        // Some old files dont have id fields. Use picture_pile for backup id.
        if (!tileData.picture_pile)
          throw new Error('Tiles must have id');
        tileID = tileData.picture_pile;
      } else {
        tileID = tileData.id;
      }
      var tile = lthi.getOrCreateTile(tileID);

      tile.history.picturePile = tileData.picture_pile;
      tile.history.contentsScale = tileData.contents_scale;

      tile.priority[0] = tileData.priority[0];
      tile.priority[1] = tileData.priority[1];
      tile.managedState = tileData.managed_state;
    }
  };


  return {
    Model: Model,
  }
});

