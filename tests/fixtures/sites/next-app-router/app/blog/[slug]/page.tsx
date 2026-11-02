const posts = [{ slug: "hello-world", title: "Hello World" }];

export function generateStaticParams() {
  return posts.map((post) => ({ slug: post.slug }));
}

export async function generateMetadata({ params }) {
  const post = posts.find((p) => p.slug === params.slug);
  return { title: post?.title };
}

export default function Post({ params }) {
  const post = posts.find((p) => p.slug === params.slug);
  return (
    <article>
      <h1>{post?.title}</h1>
    </article>
  );
}
