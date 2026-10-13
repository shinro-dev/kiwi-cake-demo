// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
//
// HOW A FACT READ FROM TWO SOURCES IS DECIDED, when one source is this tab's own fold of the event stream and the
// other is the snapshot the runtime answers a poll with.
//
// The rule this replaces was "the fold always wins over the snapshot", and it was written down as though it were a
// fact about freshness. It is not. It is a guess about arrival, and arrival is exactly the thing that races here: the
// poll runs every five seconds and the stream runs continuously, so BOTH of these happen in an ordinary session.
//
//   A RECORD FOLDED EARLIER CAN BE OLDER THAN AN ARRIVING SNAPSHOT. A tab folds the capture at record 5, the run goes
//   on for four minutes, and the snapshot that lands names the capture at record 300. Preferring the fold draws the
//   four-minute-old picture and hides the one the runtime is holding.
//   A DELAYED SNAPSHOT CAN BE OLDER THAN WHAT HAS BEEN FOLDED SINCE. A snapshot is built at the runtime's cursor and
//   then travels; records 301 to 340 reach the stream while it is in flight. Applying it over the fold puts an older
//   measurement back on the screen after a newer one was already shown.
//
// NEITHER IS SOLVED BY PREFERRING A SOURCE, because the two cases want opposite preferences. They are both solved by
// the ordering that already exists in the data: the runtime keeps ONE ordered log per runtime session, every record
// carries its `seq` in that log, and the snapshot names the `seq` of the record it is describing. So the decision is
// made on the sequence number, in both directions, and the source a value came from is reported rather than assumed.
//
// AND A SEQUENCE NUMBER ONLY MEANS ANYTHING INSIDE ONE SESSION. `runtime_session` identifies the log the numbers count
// in. A snapshot from a DIFFERENT session is not newer and not older: it is another log, whose record 5 has no
// relation whatever to this one's record 300. Comparing across them would be arithmetic on two different things, so
// the fold is RESET when the session changes rather than merged with the new one, which is what `applySnapshot` does
// before anything here is consulted. `sameSession` below is the guard that says so at the point of decision, so a
// caller that reaches this with two sessions in hand gets the snapshot alone and never a comparison.

/** Anything carried by a record of the runtime's ordered log: it has that record's sequence number. */
export interface Sequenced {
  seq: number;
}

/** Which of the two sources a decided value came from. Null when there was nothing to decide between. */
export type MergeSource = 'folded' | 'snapshot';

export interface Decided<T> {
  value: T | null;
  from: MergeSource | null;
}

/**
 * The newer of what this tab FOLDED and what the SNAPSHOT names, decided by sequence number in one ordered log.
 *
 * Whichever carries the higher `seq` is the later record of that log and wins. Equal sequence numbers are the SAME
 * record described twice, and the fold is taken for it: both describe one record, so nothing is lost either way, and
 * preferring the fold keeps the answer stable while a poll repeats the same snapshot. A source that holds nothing is
 * not a source with an old value, so the other one is taken whole.
 */
export function decideBySeq<T extends Sequenced>(folded: T | null, snapshot: T | null): Decided<T> {
  if (folded === null && snapshot === null) return { value: null, from: null };
  if (folded === null) return { value: snapshot, from: 'snapshot' };
  if (snapshot === null) return { value: folded, from: 'folded' };
  return snapshot.seq > folded.seq ? { value: snapshot, from: 'snapshot' } : { value: folded, from: 'folded' };
}

/**
 * Whether two sequence numbers may be compared at all: only inside one runtime session.
 *
 * Both arguments are what the state believes the session to be at the moment of the decision. They are equal in every
 * ordinary case, because the reducer clears the folded half of the state when a snapshot names a different session;
 * this exists so that the rule is stated where the comparison is made and not only where the reset happens.
 */
export function sameSession(foldedSession: string | null, snapshotSession: string | null): boolean {
  return foldedSession !== null && snapshotSession !== null && foldedSession === snapshotSession;
}
