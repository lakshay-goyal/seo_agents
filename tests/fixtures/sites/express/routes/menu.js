const router = require("express").Router();

router.get("/", (req, res) => res.render("menu"));
router.get("/:dish", (req, res) => res.render("dish", { dish: req.params.dish }));

module.exports = router;
