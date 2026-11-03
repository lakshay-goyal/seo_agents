<?php get_header(); ?>
<main>
  <?php if ( have_posts() ) : while ( have_posts() ) : the_post(); the_excerpt(); endwhile; endif; ?>
</main>
<?php get_footer(); ?>
