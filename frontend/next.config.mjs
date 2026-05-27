/** @type {import('next').NextConfig} */
const nextConfig = {
  // Allow the browser to connect to any backend URL the user configures
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Frame-Options', value: 'DENY' },
        ],
      },
    ];
  },
};

export default nextConfig;
