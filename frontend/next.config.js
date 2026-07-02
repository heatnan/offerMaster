/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  async rewrites() {
    const api = process.env.NEXT_PUBLIC_API_BASE || 'http://backend:8000';
    return [
      { source: '/api/backend/:path*', destination: `${api}/:path*` },
      { source: '/files/:path*', destination: `${api}/files/:path*` },
    ];
  },
};
module.exports = nextConfig;
