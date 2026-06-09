// 团队资源 API:套图模板(团队共享)。
import { api } from './client.js'

export async function listMockupTemplates() {
  return (await api.get('/team/mockup-templates')).data
}

// files: File[](多张产品照)。multipart 手动拼,支持同名多文件。
export async function createMockupTemplate(name, files) {
  const fd = new FormData()
  fd.append('name', name)
  for (const f of files) fd.append('files', f)
  return (await api.post('/team/mockup-templates', fd)).data
}

export async function deleteMockupTemplate(id) {
  return (await api.delete('/team/mockup-templates/' + id)).data
}

// 给已有模板追加图片;返回更新后的模板。
export async function addTemplateImages(id, files) {
  const fd = new FormData()
  for (const f of files) fd.append('files', f)
  return (await api.post(`/team/mockup-templates/${id}/images`, fd)).data
}

// 删除模板里的某张图;返回更新后的模板。
export async function deleteTemplateImage(id, imgId) {
  return (await api.delete(`/team/mockup-templates/${id}/images/${imgId}`)).data
}
