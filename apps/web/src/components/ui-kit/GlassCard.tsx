import React, { ReactNode, useCallback, useEffect, useMemo } from 'react';
type GlassCardProps = {
  variant: 'row' | 'col';
  children: ReactNode;
}
export function GlassCard (props:GlassCardProps): React.ReactElement {
  const {
    variant = 'col',
    children
  } = props;

  return (
    <div className={`glass-card rounded-2xl p-4 flex-1 min-w-0 [container-type:inline-size] overflow-hidden mb-6`}>
      <div className={`flex items-center justify-between gap-4 ${variant === 'row' && 'flex-row'} ${variant === 'col' && 'flex-col'}`}>
        {children}
      </div>
    </div>
  );
}