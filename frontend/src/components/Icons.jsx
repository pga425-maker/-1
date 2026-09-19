/**
 * 아이콘은 전부 인라인 stroke SVG 다. stroke-width 2, linecap/linejoin round.
 * 이모지는 쓰지 않는다(스펙 14번).
 */
const base = {
  fill: 'none',
  strokeWidth: 2,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
}

function Svg({ size = 20, color = 'currentColor', children, ...rest }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      stroke={color}
      {...base}
      {...rest}
    >
      {children}
    </svg>
  )
}

export const IconHome = (p) => (
  <Svg {...p}>
    <path d="M3 10.5 12 3l9 7.5" />
    <path d="M5.5 9.5V20h13V9.5" />
    <path d="M9.5 20v-5.5h5V20" />
  </Svg>
)

export const IconMarket = (p) => (
  <Svg {...p}>
    <path d="M3 20h18" />
    <path d="M6 20v-6" />
    <path d="M11 20V8" />
    <path d="M16 20v-9" />
    <path d="M21 20V5" />
  </Svg>
)

export const IconNews = (p) => (
  <Svg {...p}>
    <path d="M4 5.5h13a1 1 0 0 1 1 1V19H5.5A1.5 1.5 0 0 1 4 17.5z" />
    <path d="M18 9h1.5A1.5 1.5 0 0 1 21 10.5v7A1.5 1.5 0 0 1 19.5 19H18" />
    <path d="M7.5 9h6" />
    <path d="M7.5 12.5h6" />
    <path d="M7.5 16h3.5" />
  </Svg>
)

export const IconRank = (p) => (
  <Svg {...p}>
    <path d="M8 21h8" />
    <path d="M12 17v4" />
    <path d="M7 4h10v5a5 5 0 0 1-10 0z" />
    <path d="M7 5.5H4.5V7a3 3 0 0 0 3 3" />
    <path d="M17 5.5h2.5V7a3 3 0 0 1-3 3" />
  </Svg>
)

export const IconClock = ({ size = 14, color = '#FBF7F0' }) => (
  <Svg size={size} color={color}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 1.8" />
  </Svg>
)

export const IconBack = ({ size = 18, color = '#211A2E' }) => (
  <Svg size={size} color={color}>
    <path d="M15 5l-7 7 7 7" />
  </Svg>
)

export const IconChevron = ({ size = 14, color = '#5C5548' }) => (
  <Svg size={size} color={color}>
    <path d="M9 5l7 7-7 7" />
  </Svg>
)

export const IconSpeaker = ({ size = 14, color = '#211A2E' }) => (
  <Svg size={size} color={color}>
    <path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z" />
    <path d="M15.5 9.5a4 4 0 0 1 0 5" />
    <path d="M18 7.5a7 7 0 0 1 0 9" />
  </Svg>
)

export const IconAlert = ({ size = 15, color = '#C13B3B' }) => (
  <Svg size={size} color={color}>
    <path d="M12 4.5 21 19.5H3z" />
    <path d="M12 10v4" />
    <path d="M12 16.8v.2" />
  </Svg>
)

export const IconMinus = ({ size = 16, color = '#211A2E' }) => (
  <Svg size={size} color={color}>
    <path d="M6 12h12" />
  </Svg>
)

export const IconPlus = ({ size = 16, color = '#211A2E' }) => (
  <Svg size={size} color={color}>
    <path d="M12 6v12" />
    <path d="M6 12h12" />
  </Svg>
)

export const IconCheck = ({ size = 14, color = '#211A2E' }) => (
  <Svg size={size} color={color}>
    <path d="M5 12.5 10 17l9-10" />
  </Svg>
)
