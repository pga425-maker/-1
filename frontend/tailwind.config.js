/** 스펙 14번 디자인 시스템. 검증을 마친 값이라 임의로 바꾸지 않는다. */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#FBF7F0',
        card: '#FFFFFF',
        ink: '#211A2E',
        muted: '#5C5548',
        point: '#F2A93B',
        up: '#C13B3B',
        down: '#3457B2',
        tag: '#EFEAE0',
        line: 'rgba(33,26,46,0.08)',
      },
      fontFamily: {
        display: ['Black Han Sans', 'Noto Sans KR', 'sans-serif'],
        body: ['Noto Sans KR', 'sans-serif'],
      },
      borderRadius: {
        card: '16px',
        asset: '20px',
        chip: '12px',
        cta: '14px',
        tag: '20px',
      },
      boxShadow: {
        asset: '0 2px 14px rgba(33,26,46,0.07)',
      },
      spacing: {
        gutter: '20px',
      },
    },
  },
  plugins: [],
}
