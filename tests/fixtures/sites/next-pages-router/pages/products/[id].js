import Head from "next/head";

export async function getStaticPaths() {
  return { paths: [], fallback: "blocking" };
}

export async function getStaticProps({ params }) {
  return { props: { id: params.id } };
}

export default function Product({ id }) {
  return (
    <>
      <Head>
        <title>{`Product ${id}`}</title>
      </Head>
      <h1>Product details</h1>
    </>
  );
}
