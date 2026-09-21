import type { NextConfig } from 'next';
const config: NextConfig = {
  poweredByHeader: false,
  async redirects() {
    return [
      { source: '/live.html', destination: '/live', permanent: true },
      { source: '/weekly.html', destination: '/weekly', permanent: true },
      { source: '/index.html', destination: '/daily', permanent: true },
    ];
  },
};
export default config;
