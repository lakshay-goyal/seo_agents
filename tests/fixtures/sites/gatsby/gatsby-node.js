const path = require("path");
const postTemplate = path.resolve("./src/templates/post.js");

exports.createPages = async ({ graphql, actions }) => {
  const result = await graphql(`{ allMarkdownRemark { nodes { frontmatter { slug } } } }`);
  result.data.allMarkdownRemark.nodes.forEach((node) => {
    actions.createPage({
      path: `/posts/${node.frontmatter.slug}`,
      context: { slug: node.frontmatter.slug, meta: { section: "posts" } },
      component: postTemplate,
    });
  });
};
