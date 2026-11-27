import React from 'react';
import { Composition } from 'remotion';
import { ReelComposition, ReelProps } from './ReelComposition';

const defaultProps: ReelProps = {
  badge_prefix: 'STUDIO ENGINE',
  beats: [
    {
      start: 0,
      dur: 3,
      card: 'FREE AI TOOLS FOR DEVS',
      caption: "You don't need $20 monthly for AI tools.",
      mood: 'hook',
      visual: {
        type: 'terminal',
        title: 'studio ~ zsh',
        lines: ['$ npx remotion render', '-> 1080x1920 30fps ready'],
      },
    },
  ],
};

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="FactbaaziiReel"
      component={ReelComposition}
      durationInFrames={90}
      fps={30}
      width={1080}
      height={1920}
      defaultProps={defaultProps}
      calculateMetadata={({ props }) => {
        const totalSec = props.beats.reduce((sum, b) => sum + (b.dur || 2), 0);
        return {
          durationInFrames: Math.max(30, Math.round(totalSec * 30)),
          fps: 30,
          width: 1080,
          height: 1920,
        };
      }}
    />
  );
};
