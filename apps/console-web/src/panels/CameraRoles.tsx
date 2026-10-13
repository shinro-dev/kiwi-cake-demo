// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// EVERY CAMERA, EACH ANSWERING FOR ITSELF.
//
// The workspace draws ONE camera large, because watching the robot work is the demonstration. This strip draws every
// camera the runtime has, side by side, so that two feeds are visible at once and neither can be mistaken for the
// other. It is deliberately plain: the new presentation owns how this looks, and what it owns must be able to bind to
// something true. This supplies the true part.
//
// EACH ROLE ANSWERS ON ITS OWN. Its own sample, its own arrival stamp, its own state, its own measured rate. A camera
// that stalls says so under its own name while the other goes on, because the runtime keeps one source, one thread and
// one topic per role and the browser files them the same way. A tile never falls back to another role's picture: a
// camera with nothing to show says what it has instead, which is the whole point of splitting them.
//
// THE RATE IS THE RUNTIME'S MEASUREMENT, not a count taken here, and the age is the arrival of the last line on THIS
// computer's clock. They are different quantities and are labelled apart. The age of the PICTURE is not shown at all:
// the capture clock belongs to the robot computer and nothing has measured its relation to this one.

import type { ReactNode } from 'react';
import { PREVIEW_ROLES } from '../api/types';
import type { ConsoleSource } from '../api/types';
import { proxiedMediaUrl } from '../model/camera';
import { previewView } from '../model/sources';
import type { StoreSnapshot } from '../store/store';
import { Badge } from '../ui/Badge';

/** One camera's tile: the picture when there is one, and otherwise what that camera has to say. */
function RoleTile(props: { role: string; snapshot: StoreSnapshot; source: ConsoleSource; nowMs: number }): ReactNode {
  const { telemetry } = props.snapshot;
  const sample = telemetry.previews[props.role] ?? null;
  const receivedAt = telemetry.preview_received_at_ms_by_role[props.role] ?? null;
  const camera = previewView(sample, receivedAt, props.nowMs);
  // The stream is asked for BY ROLE, so the picture in this tile is the one the runtime routed on this camera's topic.
  const stream = proxiedMediaUrl(camera.streamable ? props.source.previewUrl(props.role) : null);
  const fps = camera.measured_fps;
  const age = camera.receive_age_ms;

  return (
    <div className="roletile">
      <div className="roletile__head">
        <span className="roletile__role">{props.role}</span>
        {camera.streamable && stream.url !== null ? (
          <Badge tone="ok" title="frames are arriving from this camera and reaching this browser">live</Badge>
        ) : (
          <Badge tone="warn" title="this camera has no moving picture on this screen right now">{camera.server_state ?? camera.availability}</Badge>
        )}
      </div>
      {camera.streamable && stream.url !== null ? (
        <img className="roletile__image" src={stream.url} alt={`the ${props.role} camera`} />
      ) : (
        <div className="roletile__absent">
          {stream.refusal ?? camera.reason ?? 'no preview line has reached this browser for this camera'}
        </div>
      )}
      <div className="roletile__facts">
        {/* Two different quantities, never added: what the RUNTIME measured, and when the last line reached HERE. */}
        <span>{fps === null ? 'rate not measured' : `${fps.toFixed(1)} fps measured`}</span>
        <span>{age === null ? 'no line yet' : `last line ${Math.round(age)} ms ago`}</span>
      </div>
    </div>
  );
}

/**
 * The strip. It lists the roles the runtime actually has telemetry for, in the console's reading order, and says so
 * plainly when it has none rather than drawing empty frames.
 */
export function CameraRoles(props: { snapshot: StoreSnapshot; source: ConsoleSource; nowMs: number }): ReactNode {
  const known = props.snapshot.telemetry.previews;
  const roles = PREVIEW_ROLES.filter((role) => known[role] !== undefined);
  if (roles.length === 0) {
    return <div className="roletiles roletiles--empty">No camera has sent a preview line to this browser.</div>;
  }
  return (
    <div className="roletiles">
      {roles.map((role) => (
        <RoleTile key={role} role={role} snapshot={props.snapshot} source={props.source} nowMs={props.nowMs} />
      ))}
    </div>
  );
}
