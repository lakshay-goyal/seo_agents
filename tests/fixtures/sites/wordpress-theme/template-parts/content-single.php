<article>
  <h1><?php the_title(); ?></h1>
  <p class="byline"><?php esc_html_e( 'Baked by', 'maple' ); ?> <?php the_author(); ?></p>
  <?php the_content(); ?>
</article>
