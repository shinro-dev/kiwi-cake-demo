// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// THE HEIGHT OF THE BAR THAT HOLDS THE ONLY STOP, handed down to every overlay under it.
//
// A screen whose Stop sits in a bar at the top of the view measures that bar and provides its height here; a dialog or
// drawer drawn anywhere under the screen starts its overlay at that height, so no overlay ever covers Stop (refix round 9,
// the class of verify-r8 M1). Without a provider the height is 0: the overlay starts at the top of the view.

import { createContext } from 'react';

export const StopBarHeight = createContext(0);
