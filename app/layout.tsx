import type { Metadata } from 'next';
import { Lato } from 'next/font/google';
import 'bootswatch/dist/darkly/bootstrap.min.css';
import './globals.css';

const siteUrl =
  process.env.NEXT_PUBLIC_SITE_URL ?? 'https://openreef.github.io/';

const lato = Lato({
  variable: '--font-lato',
  subsets: ['latin'],
  weight: ['400', '700'],
  style: ['normal', 'italic'],
});

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: 'openreefGPU · Cloud Reef Reconstruction',
  description:
    'Create compact 3D reef reconstructions with scale-to-zero RunPod GPU workers.',
  openGraph: {
    title: 'openreefGPU · Cloud Reef Reconstruction',
    description:
      'RunPod-accelerated OpenReef processing and interactive reef models.',
    images: [{ url: '/og.png', width: 1730, height: 908, alt: 'openreefGPU' }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'openreefGPU · Cloud Reef Reconstruction',
    description:
      'RunPod-accelerated OpenReef processing and interactive reef models.',
    images: ['/og.png'],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className={`${lato.variable} antialiased`}>{children}</body>
    </html>
  );
}
