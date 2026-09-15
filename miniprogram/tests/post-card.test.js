const path = require('path');
const { loadComponent } = require('./helpers/load-component');

const componentPath = path.join(__dirname, '../components/post-card/index.js');

describe('post-card image moderation status', () => {
  test('maps each image status to its owner-facing label', () => {
    const component = loadComponent(componentPath);

    expect(component._buildDisplayImageItems({
      displayImages: ['a.jpg', 'b.jpg', 'c.jpg'],
      image_moderation_statuses: ['pending', 'passed', 'rejected'],
    })).toEqual([
      { url: 'a.jpg', status: 'pending', label: '图片审核中' },
      { url: 'b.jpg', status: 'passed', label: '' },
      { url: 'c.jpg', status: 'rejected', label: '审核未通过' },
    ]);
  });

  test('keeps derived image items outside the post property across updates', () => {
    const post = {
      id: 1,
      images: ['cloud://bucket/a.jpg'],
      displayImages: ['https://temp.example/a.jpg'],
      image_moderation_statuses: ['passed'],
    };
    const component = loadComponent(componentPath, { post });

    component._syncDisplayImageItems(post);

    expect(component.data.post).toBe(post);
    expect(component.data.displayImageItems).toEqual([
      { url: 'https://temp.example/a.jpg', status: 'passed', label: '' },
    ]);

    const likedPost = { ...post, liked: true };
    component.data.post = likedPost;
    component._syncDisplayImageItems(likedPost);

    expect(component.data.post).toBe(likedPost);
    expect(component.data.displayImageItems[0].url).toBe('https://temp.example/a.jpg');
  });
});
