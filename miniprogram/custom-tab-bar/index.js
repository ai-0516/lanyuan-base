const auth = require('../utils/auth');

Component({
  data: {
    selected: 1,
    items: [
      {
        pagePath: '/pages/ai-chat/index',
        text: 'AI',
        iconPath: '/assets/icons/ai.png',
        selectedIconPath: '/assets/icons/ai-active.png',
        protected: true,
      },
      {
        pagePath: '/pages/feed/index',
        text: '发现',
        iconPath: '/assets/icons/feed.png',
        selectedIconPath: '/assets/icons/feed-active.png',
        protected: false,
      },
      {
        pagePath: '/pages/profile/index',
        text: '我',
        iconPath: '/assets/icons/profile.png',
        selectedIconPath: '/assets/icons/profile-active.png',
        protected: true,
      },
    ],
  },

  methods: {
    onTabTap(e) {
      const index = Number(e.currentTarget.dataset.index);
      const item = this.data.items[index];
      if (!item) return;
      if (item.protected && !auth.checkLogin(item.pagePath)) return;
      wx.switchTab({ url: item.pagePath });
    },
  },
});
