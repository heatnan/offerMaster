import './globals.css';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Offer Master — AI 模拟面试',
  description: '上传简历与 JD，AI Agent 陪你练面试',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="bg-gray-50 min-h-screen">
        <header className="bg-white border-b">
          <div className="max-w-5xl mx-auto px-6 py-4 flex items-center justify-between">
            <a href="/" className="text-xl font-bold text-blue-700">Offer 大师</a>
            <span className="text-sm text-gray-500">AI 模拟面试 · Agent Mode</span>
          </div>
        </header>
        <main className="max-w-5xl mx-auto px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
