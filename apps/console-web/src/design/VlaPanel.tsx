// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
import type { ReactNode } from 'react';
import { Button } from './Button';
import { INK, SURFACE } from './theme';
import type { VlaAction, VlaView } from '../model/vla';

const LABEL: Record<VlaAction, string> = { swap: 'Swap to the VLA policy', run: 'Run', stop: 'Stop', unswap: 'Swap back', to_mission_pose: 'Move the arm to the mission start', carry: 'Carry the block to the pad' };

export function VlaPanel({ view, onAction, busy }: { view: VlaView; onAction: (a: VlaAction) => void; busy: boolean }): ReactNode {
  if (!view.visible) return null;
  return (
    <section className="vla-panel" data-tone={view.tone} style={{ background: SURFACE.raised, border: `1px solid ${INK.rule}`, borderRadius: 12, padding: 16, display: 'flex', gap: 16, alignItems: 'center' }}>
      {view.preview ? <img src={view.preview} alt="What the policy sees" width={120} height={160} style={{ borderRadius: 8, objectFit: 'cover' }} /> : null}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, color: INK.bright }}>{view.headline}</div>
        <div style={{ color: INK.quiet }}>{view.detail}</div>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        {view.actions.map((a) => (
          <Button key={a} variant={a === 'unswap' ? 'outline' : 'secondary'} disabled={busy && a !== 'stop'} onClick={() => onAction(a)} data-control={`vla-${a}`}>
            {LABEL[a]}
          </Button>
        ))}
      </div>
    </section>
  );
}
