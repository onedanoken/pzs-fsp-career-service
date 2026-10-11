"""Atomic vendor-side operations; never operate on unrelated HR records."""
import hashlib
import json
import re
from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

FIELDS={'name','title','contacts','categories','skills','city','experience_years'}

class Partner(models.Model):
    _inherit='res.partner'
    fsp_invitation_ref=fields.Char(copy=False,index=True,groups='hr_recruitment.group_hr_recruitment_manager')
    _fsp_ref_unique=models.Constraint('UNIQUE(fsp_invitation_ref)','An FSP application reference must be unique.')

class Applicant(models.Model):
    _inherit='hr.applicant'
    fsp_invitation_ref=fields.Char(copy=False,index=True,groups='hr_recruitment.group_hr_recruitment_manager')
    fsp_candidate_ref=fields.Char(copy=False,groups='hr_recruitment.group_hr_recruitment_manager')
    fsp_profile=fields.Json(copy=False,groups='hr_recruitment.group_hr_recruitment_manager')
    fsp_payload_hash=fields.Char(copy=False,groups='hr_recruitment.group_hr_recruitment_manager')
    fsp_stage_revision=fields.Integer(default=1,copy=False)
    _fsp_ref_unique=models.Constraint('UNIQUE(fsp_invitation_ref)','An FSP application reference must be unique.')

    def write(self,values):
        result=super().write(values)
        if 'stage_id' in values:
            for record in self.sudo().filtered('fsp_invitation_ref'):
                self.env.cr.execute('UPDATE hr_applicant SET fsp_stage_revision=fsp_stage_revision+1 WHERE id=%s',[record.id])
                record.invalidate_recordset(['fsp_stage_revision'])
        return result

    def _fsp_guard(self,reference):
        if not self.env.user.has_group('hr_recruitment.group_hr_recruitment_manager'):
            raise AccessError('A dedicated recruitment manager account is required.')
        if not isinstance(reference,str) or not re.fullmatch(r'[A-Za-z0-9:_-]{12,180}',reference):
            raise ValidationError('Invalid FSP reference.')
        # Serializes retries including a lost HTTP response; create/update is one transaction.
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',[reference])

    def _fsp_owned(self,reference):
        return self.with_context(active_test=False).search([('fsp_invitation_ref','=',reference),('company_id','=',self.env.company.id)],limit=1)

    @api.model
    def fsp_upsert(self,invitation_ref,candidate_ref,job_id,profile):
        self._fsp_guard(invitation_ref)
        if not isinstance(profile,dict) or set(profile)-FIELDS or len(json.dumps(profile))>20000:
            raise ValidationError('Only the consent-selected FSP fields may be transferred.')
        job=self.env['hr.job'].browse(int(job_id)).exists()
        if not job or job.company_id!=self.env.company or not job.active:
            raise ValidationError('The job is unavailable for this company.')
        job.check_access('read')
        digest=hashlib.sha256(json.dumps(profile,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        record=self._fsp_owned(invitation_ref)
        if record and record.job_id!=job:raise ValidationError('A reference cannot be moved to a different job.')
        if not record or record.fsp_payload_hash!=digest:
            contacts=profile.get('contacts') or {}
            if not isinstance(contacts,dict):raise ValidationError('Invalid contact fields.')
            name=str(profile.get('name') or 'FSP candidate')
            if len(name)>180 or len(str(contacts.get('email') or ''))>128 or len(str(contacts.get('phone') or ''))>32:
                raise ValidationError('Contact fields exceed the supported lengths.')
            values={'partner_name':name,'job_id':job.id,'fsp_invitation_ref':invitation_ref,'fsp_candidate_ref':str(candidate_ref)[:150],'fsp_profile':profile,'fsp_payload_hash':digest}
            # Always keep a separately owned contact. Never attach or alter an existing contact
            # by matching its email, which is Odoo's usual applicant behavior.
            partner=self.env['res.partner'].with_context(active_test=False).search([('fsp_invitation_ref','=',invitation_ref),('company_id','=',self.env.company.id)],limit=1)
            partner_values={'name':name,'email':str(contacts.get('email') or '')[:128],'phone':str(contacts.get('phone') or '')[:32],'company_id':self.env.company.id,'fsp_invitation_ref':invitation_ref}
            if partner:partner.with_context(tracking_disable=True).write(partner_values)
            else:partner=self.env['res.partner'].with_context(tracking_disable=True,mail_create_nolog=True,mail_create_nosubscribe=True).create(partner_values)
            values['partner_id']=partner.id
            safe=self.with_context(tracking_disable=True,mail_create_nolog=True,mail_create_nosubscribe=True)
            if record:record.with_context(tracking_disable=True).write(values)
            else:record=safe.create(values)
        return {'id':record.id,'stage':record.stage_id.name,'stage_revision':record.fsp_stage_revision,'profile_hash':record.fsp_payload_hash}

    @api.model
    def fsp_status(self,invitation_ref):
        self._fsp_guard(invitation_ref)
        record=self._fsp_owned(invitation_ref)
        return {'id':record.id,'stage':record.stage_id.name,'stage_revision':record.fsp_stage_revision,'active':record.active} if record else None

    @api.model
    def fsp_forget(self,invitation_ref):
        self._fsp_guard(invitation_ref)
        record=self._fsp_owned(invitation_ref)
        partner=self.env['res.partner'].with_context(active_test=False).search([('fsp_invitation_ref','=',invitation_ref),('company_id','=',self.env.company.id)])
        existed=bool(record or partner)
        if record:record.unlink()
        if partner:partner.unlink()
        # A constraint failure rolls the entire transaction back. Never acknowledge it.
        return {'deleted':True,'existed':existed}
