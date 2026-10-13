// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.

import type { ReactNode } from 'react';
import { useContext, useEffect } from 'react';
import { StopBarHeight } from './stopBar';

export function Dialog(props: { title: string; onClose: () => void; children: ReactNode; actions: ReactNode }): ReactNode {
  // BELOW THE BAR THAT HOLDS THE ONLY STOP (refix round 9): the scrim starts at the bar's height, which the screen hands
  // down (`ui/stopBar.ts`), so a press on Stop reaches Stop while any dialog is open. 0 with no such bar.
  const top = useContext(StopBarHeight);
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') props.onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [props]);

  return (
    <div className="dialog-scrim" role="presentation" onClick={props.onClose} style={{ top }}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label={props.title} onClick={(event) => event.stopPropagation()}>
        <h2 className="dialog__title">{props.title}</h2>
        {props.children}
        <div className="dialog__actions">{props.actions}</div>
      </div>
    </div>
  );
}
