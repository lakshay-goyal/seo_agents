import { Routes } from '@angular/router';
import { PATHS } from './paths';
import { HomeComponent } from './home/home.component';
import { AboutComponent } from './about/about.component';

export const routes: Routes = [
  { path: PATHS.HOME, component: HomeComponent },
  { path: PATHS.ABOUT, component: AboutComponent },
  { path: '**', redirectTo: '' },
];
