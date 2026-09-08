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
    setData(update) {
      Object.assign(this.data, update)
    },
  }

  return page
}

module.exports = { loadPage }
