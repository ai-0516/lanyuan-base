function loadPage(modulePath) {
  let definition
  global.Page = options => {
    definition = options
  }

  jest.isolateModules(() => {
    require(modulePath)
  })

  if (!definition) {
    throw new Error(`Page was not registered by ${modulePath}`)
  }

  const page = {
    ...definition,
    data: { ...definition.data },
    setData(update, callback) {
      Object.assign(this.data, update)
      if (callback) callback.call(this)
    },
  }

  return page
}

module.exports = { loadPage }
