import type { Metadata } from 'next';
import 'bootswatch/dist/darkly/bootstrap.min.css';
import './globals.css';

const siteUrl =
  process.env.NEXT_PUBLIC_SITE_URL ?? 'https://openreef.github.io/';

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: 'OpenReef Cloud · GPU Reconstruction',
  description:
    'Create compact 3D reef reconstructions with scale-to-zero RunPod GPU workers.',
  openGraph: {
    title: 'OpenReef Cloud · GPU Reconstruction',
    description:
      'RunPod-accelerated OpenReef processing and interactive reef models.',
    images: [{ url: '/og.png', width: 1730, height: 908, alt: 'openreefGPU' }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'OpenReef Cloud · GPU Reconstruction',
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
      <body>{children}</body>
    </html>
  );
}
