import Head from "next/head";
import Link from "next/link";

export default function Home() {
  return (
    <>
      <Head>
        <title>Northwind Coffee</title>
        <meta name="description" content="Small-batch coffee roasted weekly." />
        <link rel="canonical" href="https://northwind.test/" />
      </Head>
      <h1>Fresh coffee, delivered</h1>
      <p>Roasted on Monday, at your door by Friday.</p>
      <Link href="/about">About us</Link>
    </>
  );
}
