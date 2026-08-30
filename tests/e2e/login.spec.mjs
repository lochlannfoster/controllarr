import { test, expect, state } from './fixtures.mjs';

test.describe('sign-in', () => {
  test('an unauthenticated visit lands on the sign-in page and the deep link survives', async ({ page }) => {
    await page.goto('/?item=movie:2');
    await expect(page).toHaveURL(/\/login\?next=/);
    await page.getByLabel('Username').fill('admin');
    await page.getByLabel('Password').fill(state().password);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL(/\/\?item=movie(:|%3A)2$/);
    await expect(page.locator('dialog.drawer #dw-title')).toHaveText('Blade Runner 2049 (2017)');
  });

  test('a wrong password is announced and nothing else changes', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Username').fill('admin');
    await page.getByLabel('Password').fill('not-it');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('alert')).toHaveText('Wrong username or password');
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByLabel('Username')).toBeVisible();
  });

  test('log out ends the session', async ({ page, login }) => {
    await login();
    await page.goto('/');
    await page.getByRole('link', { name: 'Log out' }).click();
    await expect(page).toHaveURL(/\/login$/);
    await page.goto('/');
    await expect(page).toHaveURL(/\/login$/);
  });

  test('a standard user is kept out of Settings', async ({ page, login }) => {
    await login();
    await page.request.post('/api/users', { data: { username: 'viewer', password: 'viewer-pw', role: 'user' } });
    await page.context().clearCookies();
    await login('viewer', 'viewer-pw');
    await page.goto('/settings');
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator('.secnav a[data-admin]')).toBeHidden();
  });
});
