import * as React from "react";

export default function Post({ pageContext }) {
  return <h1>{pageContext.slug}</h1>;
}
