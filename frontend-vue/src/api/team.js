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
