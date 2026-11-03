const express = require("express");
const menuRouter = require("./routes/menu");

const app = express();
app.set("view engine", "ejs");
app.use("/menu", menuRouter);

app.get("/", async (req, res) => {
  const specials = await loadSpecials({
    includeSeasonal: true,
    limit: 3,
  });
  // A long handler body: the render call is far from the route declaration, which the
  // old 400-character window missed. Keep this comment long enough to prove the point.
  // ----------------------------------------------------------------------------------
  // ----------------------------------------------------------------------------------
  // ----------------------------------------------------------------------------------
  // ----------------------------------------------------------------------------------
  res.render("home", { specials });
});

app.get("/api/specials", (req, res) => res.json([]));

app.listen(process.env.PORT || 3000);
