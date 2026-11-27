import React from 'react';
import {
  AbsoluteFill,
  Audio,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

export interface BeatVisual {
  type: 'terminal' | 'comparison' | 'ui_steps' | 'metric';
  title?: string;
  lines?: string[];
  left_title?: string;
  left_val?: string;
  right_title?: string;
  right_val?: string;
  steps?: string[];
  active?: number;
  value?: string;
  label?: string;
}

export interface Beat {
  start?: number;
  dur: number;
  card: string;
  caption: string;
  mood?: string;
  visual?: BeatVisual;
}

export interface ReelProps {
  badge_prefix?: string;
  audio_file?: string;
  beats: Beat[];
}

const MOODS: Record<string, { top: string; bot: string; acc: string }> = {
  hook: { top: '#1c0c3e', bot: '#62208a', acc: '#c373ff' },
  cool: { top: '#0e1230', bot: '#262e6c', acc: '#6ea5ff' },
  dark: { top: '#0c0c1a', bot: '#1c1636', acc: '#8273af' },
  rise: { top: '#12103a', bot: '#442484', acc: '#a582ff' },
  flat: { top: '#101222', bot: '#222642', acc: '#8c9bc3' },
  hero: { top: '#200e48', bot: '#6e2c9c', acc: '#e19bff' },
  loop: { top: '#140c30', bot: '#411969', acc: '#b973ff' },
};

export const ReelComposition: React.FC<ReelProps> = ({
  badge_prefix = 'DEV TOOLS',
  audio_file,
  beats,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const t = frame / fps;

  // Locate active beat
  let clock = 0;
  let activeIdx = beats.length - 1;
  let beatStart = 0;
  for (let i = 0; i < beats.length; i++) {
    if (t < clock + beats[i].dur || i === beats.length - 1) {
      activeIdx = i;
      beatStart = clock;
      break;
    }
    clock += beats[i].dur;
  }

  const beat = beats[activeIdx];
  const elapsed = Math.max(0, t - beatStart);
  const localFrame = Math.max(0, Math.round(elapsed * fps));
  const mood = MOODS[beat.mood || 'cool'] || MOODS.cool;

  const pop = spring({
    frame: localFrame,
    fps,
    config: { damping: 14, stiffness: 160, mass: 0.7 },
  });

  const slideY = interpolate(pop, [0, 1], [28, 0]);
  const words = beat.caption.split(' ');
  const wordIdx = Math.min(
    words.length - 1,
    Math.floor((elapsed / Math.max(0.4, beat.dur * 0.9)) * words.length)
  );

  const vis = beat.visual || {
    type: 'metric',
    value: 'FREE',
    label: beat.card,
  };

  return (
    <AbsoluteFill
      style={{
        background: `linear-gradient(180deg, ${mood.top} 0%, ${mood.bot} 100%)`,
        fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, sans-serif',
        color: '#ffffff',
      }}
    >
      {audio_file ? <Audio src={staticFile(audio_file)} /> : null}

      {/* Subtle animated grid */}
      <AbsoluteFill
        style={{
          backgroundImage:
            'linear-gradient(to right, rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(to bottom, rgba(255,255,255,0.03) 1px, transparent 1px)',
          backgroundSize: '90px 90px',
          backgroundPosition: `0px ${(t * 18) % 90}px`,
        }}
      />

      {/* Safe-zone container (x=90..835, y=240..1430) */}
      <div
        style={{
          position: 'absolute',
          left: 90,
          top: 245,
          width: 745,
          height: 1170,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
        }}
      >
        <div>
          {/* Top badge */}
          <div
            style={{
              fontSize: 26,
              fontWeight: 700,
              letterSpacing: 1.5,
              color: mood.acc,
              paddingBottom: 14,
              borderBottom: '2px solid rgba(255,255,255,0.2)',
            }}
          >
            {badge_prefix} // BEAT {String(activeIdx + 1).padStart(2, '0')}/
            {String(beats.length).padStart(2, '0')}
          </div>

          {/* Headline card */}
          <div
            style={{
              marginTop: 32,
              transform: `translateY(${slideY}px)`,
              opacity: activeIdx === 0 ? 1 : pop,
              borderLeft: `8px solid ${mood.acc}`,
              paddingLeft: 24,
              fontSize: 72,
              fontWeight: 900,
              lineHeight: 1.08,
              textShadow: '0 6px 24px rgba(0,0,0,0.45)',
            }}
          >
            {beat.card}
          </div>

          {/* Glassmorphism B-roll / Widget Stage */}
          <div
            style={{
              marginTop: 38,
              height: 330,
              borderRadius: 24,
              background: 'rgba(12, 14, 30, 0.78)',
              border: `2px solid ${mood.acc}88`,
              backdropFilter: 'blur(14px)',
              padding: 28,
              boxShadow: '0 24px 50px rgba(0,0,0,0.4)',
              transform: `scale(${0.96 + 0.04 * pop})`,
            }}
          >
            {vis.type === 'terminal' && (
              <div>
                <div style={{ display: 'flex', gap: 10, marginBottom: 20 }}>
                  <span style={{ width: 14, height: 14, borderRadius: 7, background: '#ff5f56' }} />
                  <span style={{ width: 14, height: 14, borderRadius: 7, background: '#ffbd2e' }} />
                  <span style={{ width: 14, height: 14, borderRadius: 7, background: '#27c93f' }} />
                  <span style={{ marginLeft: 12, fontSize: 22, color: '#a8b2d1' }}>
                    {vis.title || 'terminal'}
                  </span>
                </div>
                {(vis.lines || []).map((ln, idx) => (
                  <div
                    key={idx}
                    style={{
                      fontFamily: 'monospace',
                      fontSize: 28,
                      lineHeight: 1.45,
                      color: ln.startsWith('$')
                        ? mood.acc
                        : ln.startsWith('->')
                          ? '#78f0aa'
                          : '#e6ecff',
                    }}
                  >
                    {ln}
                  </div>
                ))}
              </div>
            )}

            {vis.type === 'comparison' && (
              <div style={{ display: 'flex', gap: 18, height: '100%' }}>
                <div
                  style={{
                    flex: 1,
                    borderRadius: 18,
                    padding: 24,
                    background: 'rgba(255,255,255,0.04)',
                    border: '1px solid rgba(255,255,255,0.15)',
                  }}
                >
                  <div style={{ fontSize: 24, color: '#9ca3af' }}>{vis.left_title || 'PAID'}</div>
                  <div
                    style={{
                      marginTop: 22,
                      fontSize: 64,
                      fontWeight: 900,
                      color: '#fca5a5',
                      textDecoration: 'line-through',
                    }}
                  >
                    {vis.left_val || '$20/mo'}
                  </div>
                </div>
                <div
                  style={{
                    flex: 1,
                    borderRadius: 18,
                    padding: 24,
                    background: 'rgba(139, 92, 246, 0.16)',
                    border: `2px solid ${mood.acc}`,
                  }}
                >
                  <div style={{ fontSize: 24, color: mood.acc }}>{vis.right_title || 'FREE'}</div>
                  <div style={{ marginTop: 22, fontSize: 64, fontWeight: 900 }}>
                    {vis.right_val || '$0'}
                  </div>
                </div>
              </div>
            )}

            {vis.type === 'ui_steps' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                <div style={{ fontSize: 24, color: mood.acc, fontWeight: 700 }}>
                  {vis.title || 'Quick Setup'}
                </div>
                {(vis.steps || []).slice(0, 3).map((s, i) => (
                  <div
                    key={i}
                    style={{
                      padding: '14px 20px',
                      borderRadius: 14,
                      fontSize: 30,
                      background:
                        i === (vis.active ?? 0)
                          ? 'rgba(139, 92, 246, 0.35)'
                          : 'rgba(255,255,255,0.05)',
                      border:
                        i === (vis.active ?? 0)
                          ? `2px solid ${mood.acc}`
                          : '1px solid rgba(255,255,255,0.1)',
                    }}
                  >
                    {s}
                  </div>
                ))}
              </div>
            )}

            {vis.type === 'metric' && (
              <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', height: '100%' }}>
                <div style={{ fontSize: 104, fontWeight: 900, lineHeight: 1 }}>{vis.value || 'FREE'}</div>
                <div style={{ marginTop: 18, fontSize: 28, color: mood.acc, fontWeight: 700 }}>
                  {vis.label || beat.card}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Karaoke Word-Highlight Caption Box */}
        <div>
          <div
            style={{
              background: 'rgba(10, 8, 24, 0.72)',
              border: `2px solid ${mood.acc}77`,
              borderRadius: 18,
              padding: '20px 24px',
              fontSize: 40,
              fontWeight: 700,
              lineHeight: 1.45,
              display: 'flex',
              flexWrap: 'wrap',
              gap: '8px 12px',
            }}
          >
            {words.map((w, i) => (
              <span
                key={i}
                style={{
                  padding: '2px 10px',
                  borderRadius: 10,
                  background: i === wordIdx ? mood.acc : 'transparent',
                  color: i <= wordIdx ? '#ffffff' : '#a8aec8',
                }}
              >
                {w}
              </span>
            ))}
          </div>

          {/* Bottom progress bar */}
          <div
            style={{
              marginTop: 28,
              height: 8,
              width: '100%',
              background: 'rgba(255,255,255,0.18)',
              borderRadius: 4,
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                height: '100%',
                width: `${Math.min(100, (frame / durationInFrames) * 100)}%`,
                background: mood.acc,
              }}
            />
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};
