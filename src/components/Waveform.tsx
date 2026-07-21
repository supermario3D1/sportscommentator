interface WaveformProps {
  values: number[];
  activeIndex?: number;
}

export function Waveform({ values, activeIndex }: WaveformProps) {
  const safe = values.length > 0 ? values : Array.from({ length: 48 }, () => 0.08);
  return (
    <div className="waveform" aria-label="Commentary waveform visualization">
      {safe.map((value, index) => (
        <span
          key={`${index}-${value}`}
          className={activeIndex !== undefined && index <= activeIndex ? 'is-active' : undefined}
          style={{ height: `${Math.max(8, value * 68)}px` }}
        />
      ))}
    </div>
  );
}
