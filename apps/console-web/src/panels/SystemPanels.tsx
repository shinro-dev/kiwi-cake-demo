// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// Which software is responsible, and what Cake does and does not do today.
//
// Components are shown in four separate layers, because they are four different kinds of thing: a Python function is a
// logical component, not a process; a process is not a device; and no component in this execution path is a Cake
// module. The ladder is five rungs, each claimed on its own evidence, and a rung whose value is null reads as NOT
// DETERMINED, never as a no.
//
// THREE CAKE FACTS ARE KEPT APART, because they are three and a console that shows one badge for them will be read as
// claiming all three: whether a resident was reachable, whether any slot exists, and whether THIS task used one. A
// reachable resident does not imply a slot, and a slot does not imply that the task in front of you went anywhere near
// it. Each line carries the evidence the claim rests on, and the evidence is the claim: the name of a rung is not.
//
// NOT PARTICIPATING IS NOT A PASS AND NOT A FAULT. Cake takes no part in this execution path today, by design. A green
// tick over that sentence would read as "Cake checked out fine", which is a claim nobody made, so the neutral tone
// carries it and the words say exactly what is true.

import type { ReactNode } from 'react';
import type { CakeContinuity, CakeLadder, CakeUpdatePreview, Capability } from '@kiwi/demo-contracts';
import { CAKE_TASK_SENTENCE, cakeTaskSentence } from '../model/cakeTask';
import { digestPrefix, millis, textOr } from '../model/format';
import type { MissionRecord } from '../model/missionRecord';
import { reasonCodeLabel } from '../model/reason';
import type { RobotStatus } from '../model/robotStatus';
import type { ViewState } from '../model/types';
import { Badge, healthTone } from '../ui/Badge';
import { IconBlocked, IconFalse, IconTrue, IconUnknown } from '../ui/Icons';
import { Panel } from '../ui/Panel';

/** The sentence this demo states about Cake beside a task Cake took no part in. Read from the mission otherwise: `model/cakeTask.ts`. */
export { CAKE_TASK_SENTENCE };

/** The label every physical Cake update control carries, on the control itself. */
export const CAKE_ADAPTER_LABEL = 'Requires Cake adapter';

const LAYER_LABEL: Record<string, string> = {
  logical_python: 'logical Python component',
  process: 'process',
  device: 'device',
  cake_module: 'Cake module',
};

export function ComponentsPanel(props: { view: ViewState }): ReactNode {
  const groups = ['logical_python', 'process', 'device', 'cake_module'] as const;
  return (
    <Panel
      title="components"
      extra={<Badge tone="quiet">{String(props.view.components.length)} reported</Badge>}
      note="Health is a claim; the evidence column is what the claim rests on. A component with no evidence is reported as unknown."
      flush
    >
      <table className="table">
        <thead>
          <tr>
            <th>layer</th>
            <th>component</th>
            <th>health</th>
            <th>active</th>
            <th>evidence</th>
          </tr>
        </thead>
        <tbody>
          {groups.flatMap((layer) =>
            props.view.components
              .filter((component) => component.layer === layer)
              .map((component) => (
                <tr key={component.id}>
                  <td className="muted">{LAYER_LABEL[layer]}</td>
                  <td>
                    {component.id}
                    {component.cake_slot == null ? null : <span className="muted"> slot {component.cake_slot.slot_id}</span>}
                  </td>
                  <td>
                    <Badge tone={healthTone(component.health)} dot>
                      {component.health}
                    </Badge>
                  </td>
                  <td>{component.active === null ? 'not determined' : component.active ? 'yes' : 'no'}</td>
                  <td className="wrap">{component.evidence}</td>
                </tr>
              )),
          )}
          {props.view.components.length === 0 ? (
            <tr>
              <td colSpan={5} className="wrap">
                No snapshot has reported a component yet.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </Panel>
  );
}

const RUNGS: [keyof Omit<CakeLadder, 'synthetic'>, string][] = [
  ['artifacts_available', 'artifacts available'],
  ['resident_reachable', 'resident reachable'],
  ['admin_interface_compatible', 'admin interface compatible'],
  ['modules_active', 'modules active'],
  ['mission_participation', 'mission participation'],
];

/**
 * yes / no / not determined, each with its own glyph so the answer is never carried by colour alone.
 *
 * A NO IS NEUTRAL HERE. These rungs are facts about what is connected today, not health: "no resident is reachable" is
 * the state this demo is designed to be in, and painting it as a failure would send somebody looking for a fault that
 * does not exist. A yes is the one that changes something, so a yes is the one that is coloured.
 */
function RungValue(props: { value: boolean | null }): ReactNode {
  if (props.value === null) {
    return (
      <Badge tone="quiet" icon={<IconUnknown />}>
        not determined
      </Badge>
    );
  }
  if (props.value) {
    return (
      <Badge tone="ok" icon={<IconTrue />}>
        yes
      </Badge>
    );
  }
  return (
    <Badge tone="quiet" icon={<IconFalse />}>
      no
    </Badge>
  );
}

/** Three-state, for a contract field that is a boolean or null. A null is never drawn as a no. */
function Tri(props: { value: boolean | null }): ReactNode {
  return <RungValue value={props.value} />;
}

/**
 * The physical Cake controls. They are pre-declared, disabled, and each says on itself what it is waiting for.
 *
 * `cake_update` is the one of them this demo performs another way: the capability slot check card in the Cake panel
 * applies a prepared Plan to the robot's capability slot and reads it back (POST /api/capability/check), and the
 * mission's capability request activates one while a mission waits. The row says so, so that a control that is always
 * off does not read as "no module update exists here".
 */
const CAKE_CONTROLS: { capability: string; label: string; note?: string }[] = [
  { capability: 'cake_update_preview', label: 'update preview' },
  {
    capability: 'cake_update',
    label: 'deploy admitted candidate',
    note:
      'The live module update this demo performs is the capability slot check card in the Cake panel, which applies a prepared Plan to the capability slot and reads it back; this row is the runtime\'s own cake_update capability and stays off until the runtime reports it enabled.',
  },
  { capability: 'cake_transaction_progress', label: 'transaction progress' },
  { capability: 'cake_affected_modules', label: 'affected-module view' },
  { capability: 'cake_rollback', label: 'rollback transaction' },
  { capability: 'cake_backend_select', label: 'select Cake backend' },
];

function serverReason(capability: Capability | null): string {
  return capability === null
    ? 'this runtime does not report this capability at all, so there is nothing to wait on yet'
    : textOr(capability.reason ?? reasonCodeLabel(capability.reason_code));
}

function CakePreview(props: { preview: CakeUpdatePreview }): ReactNode {
  const preview = props.preview;
  return (
    <>
      <div className="field__label">update preview</div>
      <table className="table">
        <tbody>
          <tr>
            <td>bound to</td>
            <td className="wrap">
              candidate {digestPrefix(preview.candidate_digest)}, plan {digestPrefix(preview.plan_digest)}, epoch {String(preview.epoch)}. A change
              to any of these three makes this preview say nothing about the next one.
            </td>
          </tr>
          <tr>
            <td>affected slots</td>
            <td className="wrap">{preview.affected_slots.length === 0 ? 'none stated' : preview.affected_slots.join(', ')}</td>
          </tr>
          <tr>
            <td>resource implications</td>
            <td className="wrap">{preview.resource_implications.length === 0 ? 'none stated' : preview.resource_implications.join('; ')}</td>
          </tr>
          <tr>
            <td>expected interruptions</td>
            <td className="wrap">
              camera <Tri value={preview.expected_interruptions.camera} /> connection <Tri value={preview.expected_interruptions.connection} />{' '}
              actuator <Tri value={preview.expected_interruptions.actuator} />
            </td>
          </tr>
        </tbody>
      </table>
    </>
  );
}

function CakeContinuityBlock(props: { continuity: CakeContinuity }): ReactNode {
  const continuity = props.continuity;
  return (
    <>
      <div className="field__label">continuity, as it was measured across the transaction</div>
      <table className="table">
        <tbody>
          <tr>
            <td>mission restarted</td>
            <td>
              <Tri value={continuity.mission_restarted} />
            </td>
            <td>motor owner restarted</td>
            <td>
              <Tri value={continuity.motor_owner_restarted} />
            </td>
          </tr>
          <tr>
            <td>update induced hold</td>
            <td>
              <Tri value={continuity.update_induced_hold} />
            </td>
            <td>completed</td>
            <td>
              <Tri value={continuity.completed} />
            </td>
          </tr>
          <tr>
            <td>largest command gap</td>
            <td>{millis(continuity.max_command_gap_ms)}</td>
            <td>largest observation gap</td>
            <td>{millis(continuity.max_observation_gap_ms)}</td>
          </tr>
          <tr>
            <td>affected module interruption</td>
            <td>{millis(continuity.affected_module_interruption_ms)}</td>
            {/* The dwell the mission would have made anyway, kept SEPARATE from the gaps so that an interruption can
                be compared with it rather than hidden inside it. */}
            <td>natural dwell, on its own</td>
            <td>{millis(continuity.natural_dwell_ms)}</td>
          </tr>
          <tr>
            <td>measured from</td>
            <td colSpan={3} className="wrap">
              {continuity.sources.map((source) => `${source.name} (outside the replaced module)`).join(', ')}
            </td>
          </tr>
        </tbody>
      </table>
    </>
  );
}

/**
 * THE RESIDENT'S OWN SLOTS, as GET /api/robot lists them: the reading the main screen's live slot comes from (verdict
 * 2026-09-27 item 11). The runtime snapshot's Cake view (`cake.slots`) is another source, and on a robot-resident
 * deployment it lists none while the resident lists three; its count is never printed in the resident's name. Null count:
 * the resident's list was not read, and the words say why.
 */
export function residentSlots(robot: RobotStatus | null | undefined, robotError: string | null | undefined): { count: number | null; words: string } {
  if (robot === null || robot === undefined) return { count: null, words: robotError === null || robotError === undefined ? 'not read yet' : `not read: ${robotError}` };
  if (!robot.configured) return { count: null, words: 'no robot is configured on this runtime, so no resident slot is read' };
  const items = robot.slots.items;
  if (items.length === 0 && robot.slots.reason_code !== null) return { count: null, words: `not listed: ${robot.slots.reason_code}` };
  const active = items.filter((item) => item.state === 'ACTIVE');
  const activeWords =
    active.length === 0
      ? 'None of them is active in the resident.'
      : `Active in the resident: ${active.map((item) => `${item.name} (generation ${item.generation === null ? 'not reported' : String(item.generation)})`).join(', ')}.`;
  return { count: items.length, words: `${activeWords}${robot.slots.stale ? ' The runtime marks this listing stale.' : ''}` };
}

export function CakePanel(props: { view: ViewState; mission?: MissionRecord | null; robot?: RobotStatus | null; robotError?: string | null }): ReactNode {
  const view = props.view;
  const listed = residentSlots(props.robot, props.robotError);
  const cake = view.cake;
  const participation = cake.ladder.mission_participation;
  const resident = cake.ladder.resident_reachable;
  const byId = new Map<string, Capability>(view.capabilities.map((capability) => [capability.id, capability]));
  const modules = view.components.filter((component) => component.layer === 'cake_module');
  const usingSlots = modules.filter((component) => component.cake_slot != null && component.active === true);
  const nothingReported =
    cake.slots.length === 0 && cake.transactions.length === 0 && cake.update_preview === null && cake.continuity === null;

  return (
    <Panel
      title="cake"
      state={participation.value === true ? 'active' : 'idle'}
      stateWords={
        participation.value === null
          ? 'Participation not determined'
          : participation.value
            ? 'Taking part in this task'
            : 'Not connected to this task'
      }
      extra={
        <>
          {/* NO BADGE A SNAPSHOT DID NOT STATE (verify-r1 must_fix 3): the fold's default Cake view is not the runtime's word. */}
          {view.snapshot_at_ms === null ? (
            <Badge tone="quiet">not read</Badge>
          ) : cake.synthetic ? (
            <Badge tone="synthetic">synthetic</Badge>
          ) : (
            <Badge tone="info">read from a resident</Badge>
          )}
          <Badge tone="quiet">{listed.count === null ? `resident slots ${listed.words}` : `${String(listed.count)} slot(s) the resident lists`}</Badge>
        </>
      }
      note="Each rung is claimed separately. A higher rung never implies that a lower one was checked, and a rung with no value was not determined."
    >
      {/* The sentence, read from the mission record (`model/cakeTask.ts`). It is the first thing on this card because
          it is the thing an operator most often gets wrong about this demo, and it is never drawn in the colour of a
          passing check. */}
      <div className="banner">
        {participation.value === false
          ? cakeTaskSentence(props.mission ?? null)
          : participation.value === null
            ? 'The runtime has not determined whether a Cake module takes part in this task.'
            : `A Cake module takes part in this task: ${participation.evidence}`}
      </div>

      <div className="field__label" style={{ marginTop: 'var(--space-2)' }}>
        three separate facts, each on its own evidence
      </div>
      <table className="table">
        <tbody>
          <tr>
            <td>a resident this runtime could reach</td>
            <td>
              <RungValue value={resident.value} />
            </td>
            <td className="wrap">
              {resident.evidence} This console never infers a resident from a process table: what is here is the
              runtime's claim and the evidence the runtime gave for it.
            </td>
          </tr>
          <tr>
            <td>slots the resident reported</td>
            <td>
              <Badge tone="quiet">{listed.count === null ? listed.words : String(listed.count)}</Badge>
            </td>
            <td className="wrap">
              counted from the slots the robot&apos;s resident lists (GET /api/robot), the reading the main screen&apos;s live
              slot comes from. {listed.count === null ? null : `${listed.words} `}A reachable resident does
              not imply a slot and this number is never derived from a rung.
            </td>
          </tr>
          <tr>
            <td>a task using those slots</td>
            <td>
              <RungValue value={participation.value} />
            </td>
            <td className="wrap">
              {participation.evidence}{' '}
              {usingSlots.length === 0
                ? "The runtime's snapshot lists no component of this execution path as an active Cake module."
                : `The runtime's snapshot lists these components as active Cake modules: ${usingSlots.map((component) => component.id).join(', ')}.`}
            </td>
          </tr>
        </tbody>
      </table>

      <div className="field__label" style={{ marginTop: 'var(--space-2)' }}>
        the integration ladder
      </div>
      {RUNGS.map(([key, label]) => {
        const rung = cake.ladder[key];
        return (
          <div className="rung" key={key}>
            <span className="rung__name">{label}</span>
            <span>
              <RungValue value={rung.value} />
            </span>
            <span className="rung__evidence">{rung.evidence}</span>
          </div>
        );
      })}

      <div className="field__label" style={{ marginTop: 'var(--space-2)' }}>
        physical update controls
      </div>
      {CAKE_CONTROLS.map((control) => {
        const capability = byId.get(control.capability) ?? null;
        return (
          <div className="control-row" key={control.capability}>
            <div className="control-row__text">
              <div className="control-row__name">
                <IconBlocked /> {control.label}
              </div>
              <div className="control-row__reason">
                <span className="muted">{control.capability}</span>: {serverReason(capability)}
              </div>
              {control.note === undefined ? null : <div className="control-row__reason">{control.note}</div>}
            </div>
            {/* The label states what the control is waiting for; the reason above it stays the SERVER's own. Nothing
                here is ever enabled by this console. */}
            <button type="button" className="button" disabled title={serverReason(capability)}>
              {CAKE_ADAPTER_LABEL}
            </button>
          </div>
        );
      })}

      {nothingReported ? (
        <p className="muted" style={{ marginTop: 'var(--space-2)' }}>
          No slot, transaction, update preview or continuity measurement has been reported in the runtime snapshot&apos;s
          Cake view. That is one absence stated once, and not four empty tables; the resident&apos;s own slots are counted
          above, from the resident.
        </p>
      ) : null}

      {cake.slots.length === 0 ? null : (
        <>
          <div className="field__label" style={{ marginTop: 'var(--space-2)' }}>
            slots in the runtime snapshot&apos;s Cake view
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>slot</th>
                <th>lifecycle</th>
                <th>generation</th>
                <th>capsule</th>
                <th>config</th>
                <th>process session</th>
              </tr>
            </thead>
            <tbody>
              {cake.slots.map((slot) => (
                <tr key={slot.slot_id}>
                  <td>{slot.slot_id}</td>
                  <td>{slot.lifecycle_state}</td>
                  <td>{String(slot.generation)}</td>
                  <td className="muted">{digestPrefix(slot.capsule_digest)}</td>
                  <td className="muted">{digestPrefix(slot.config_digest)}</td>
                  <td className="muted">{textOr(slot.process_session)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {cake.transactions.length === 0 ? null : (
        <>
          <div className="field__label" style={{ marginTop: 'var(--space-2)' }}>
            transactions
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>transaction</th>
                <th>kind</th>
                <th>phase</th>
                <th>committed</th>
                <th>durable</th>
                <th>candidate</th>
              </tr>
            </thead>
            <tbody>
              {cake.transactions.map((transaction) => (
                <tr key={transaction.transaction_id}>
                  <td className="muted">{transaction.transaction_id}</td>
                  <td>{transaction.kind}</td>
                  <td>{transaction.phase}</td>
                  {/* Committed and durable are two facts and are never merged into one badge. */}
                  <td>
                    <Tri value={transaction.committed} />
                  </td>
                  <td>
                    <Tri value={transaction.durable} />
                  </td>
                  <td className="muted">{digestPrefix(transaction.candidate_digest ?? null)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {cake.transactions.flatMap((transaction) =>
            transaction.native_events.map((nativeEvent, index) => (
              <div className="muted" style={{ fontSize: 'var(--text-xs)' }} key={`${transaction.transaction_id}-${String(index)}`}>
                {transaction.transaction_id} <code>{nativeEvent.code}</code> {nativeEvent.text}
              </div>
            )),
          )}
        </>
      )}

      {cake.update_preview === null ? null : <CakePreview preview={cake.update_preview} />}
      {cake.continuity === null ? null : <CakeContinuityBlock continuity={cake.continuity} />}
    </Panel>
  );
}

/**
 * Controls that do not exist yet, pre-populated and disabled, each with the capability it is waiting on and the
 * server's own reason. An entry the runtime does not report at all says exactly that, rather than inventing a reason.
 *
 * The Cake update controls are NOT here any more: they belong beside the task, on the Cake card, which is the one
 * place an operator looks to find out what Cake is doing in the run in front of them.
 */
const FUTURE_CONTROLS: { capability: string; label: string }[] = [
  { capability: 'observer_attachment', label: 'observer attachment' },
  { capability: 'health_trace', label: 'richer health and trace' },
  { capability: 'multi_cube', label: 'multi-cube mission' },
  { capability: 'wide_search', label: 'wide search' },
  { capability: 'larger_offsets', label: 'larger placement offsets' },
  { capability: 'pad_orientations', label: 'other pad orientations' },
  { capability: 'continuous_jog', label: 'continuous jog' },
  { capability: 'pause_resume', label: 'pause and resume' },
];

export function FuturePanel(props: { view: ViewState }): ReactNode {
  const byId = new Map<string, Capability>(props.view.capabilities.map((capability) => [capability.id, capability]));
  return (
    <Panel
      title="not available yet"
      extra={<Badge tone="quiet">{String(FUTURE_CONTROLS.length)} controls</Badge>}
      note="Every one of these is driven by a server capability. None is enabled by this console, and none of them is shown as a gauge with a made-up number."
      flush
    >
      <table className="table">
        <thead>
          <tr>
            <th>control</th>
            <th>capability</th>
            <th>state</th>
            <th>reason from the server</th>
          </tr>
        </thead>
        <tbody>
          {FUTURE_CONTROLS.map((control) => {
            const capability = byId.get(control.capability) ?? null;
            return (
              <tr key={control.capability}>
                <td>
                  <IconBlocked /> {control.label}
                </td>
                <td className="muted">{control.capability}</td>
                <td>
                  <Badge tone={capability === null ? 'quiet' : capability.state === 'blocked' ? 'warn' : 'quiet'}>
                    {capability === null ? 'not reported' : capability.state}
                  </Badge>
                </td>
                <td className="wrap">{serverReason(capability)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Panel>
  );
}

export function EvidenceSummary(props: { view: ViewState }): ReactNode {
  const view = props.view;
  return (
    <Panel title="record integrity" flush>
      <table className="table">
        <tbody>
          <tr>
            <td>retained from seq</td>
            <td>{String(view.retained_from_seq)}</td>
            <td className="wrap">the oldest record this runtime can still serve</td>
          </tr>
          <tr>
            <td>cursor</td>
            <td>{String(view.seq_cursor)}</td>
            <td className="wrap">the newest record this console has folded</td>
          </tr>
          <tr>
            <td>stream gaps</td>
            <td>{String(view.stream_gaps.length)}</td>
            <td className="wrap">
              {view.stream_gaps.length === 0
                ? 'none: every seq from the cursor on arrived in order'
                : view.stream_gaps.map((gap) => `${String(gap.from_seq)} to ${String(gap.to_seq)} (${gap.declared ? 'announced' : 'detected'})`).join(', ')}
            </td>
          </tr>
          <tr>
            <td>dropped telemetry</td>
            <td>{String(view.evidence.dropped_telemetry)}</td>
            <td className="wrap">
              {view.dropped_telemetry_events.length === 0 ? 'nothing reported' : view.dropped_telemetry_events.map((item) => `${String(item.count)} at ${item.stage}`).join(', ')}
            </td>
          </tr>
          <tr>
            <td>drop notices</td>
            <td>{String(view.counters.drop_notices)}</td>
            <td className="wrap">times the server said it dropped records for this reader</td>
          </tr>
          <tr>
            <td>session evidence</td>
            <td>{view.evidence.complete ? 'complete' : `${String(view.evidence.gaps)} gap(s)`}</td>
            <td className="wrap">counted across this runtime session, not just this run</td>
          </tr>
          <tr>
            <td>runtime session</td>
            <td>{textOr(view.runtime_session)}</td>
            <td className="wrap">{view.runtime_session === null ? 'no snapshot has been read yet' : 'a change here means the runtime restarted'}</td>
          </tr>
        </tbody>
      </table>
    </Panel>
  );
}
